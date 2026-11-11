import json
import sqlite3
import unittest
from unittest.mock import patch
from outnews.core import initialize, create_event, fingerprint, canonical_url
from outnews.telegram import publish

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); initialize(self.db)
        self.event,self.devices=create_event(self.db,'خبر','https://example.com/item', ['تشبیه','کنایه','تضاد'])
        self.sentences=['این یک خبر تازه است.','شرکت آن را اعلام کرده.','جزئیات هنوز روشن نیست.']
        self.h=fingerprint(self.sentences,self.devices)
        with self.db:
            self.draft=self.db.execute('INSERT INTO drafts(event_id,sentences,body_hash,review,status) VALUES(?,?,?,?,?)',(self.event,json.dumps(self.sentences),self.h,json.dumps({'approved':True,'body_hash':self.h,'issues':[]}),'ready')).lastrowid
            self.db.execute('INSERT INTO publication_checks(draft_id,details) VALUES(?,?)',(self.draft,json.dumps({'body_hash':self.h,'result':'new','url':canonical_url('https://example.com/item'),'history_revision':0})))
    def test_uncertain_never_retried(self):
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaises(RuntimeError):publish(self.db,self.draft,'dummy','-123')
        self.assertEqual(self.db.execute('SELECT state FROM deliveries').fetchone()[0],'uncertain')
        with self.assertRaises(ValueError):publish(self.db,self.draft,'dummy','-123')
    def test_missing_or_stale_audit_blocks(self):
        self.db.execute('DELETE FROM publication_checks');self.db.commit()
        with self.assertRaises(ValueError):publish(self.db,self.draft,'dummy','-123')
    def test_changed_draft_blocks(self):
        self.db.execute('UPDATE drafts SET sentences=?',(json.dumps(['خبر تغییر کرد.']*3),));self.db.commit()
        with self.assertRaises(ValueError):publish(self.db,self.draft,'dummy','-123')

    def test_success_and_duplicate_source_are_blocked(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"ok":true,"result":{"message_id":8,"chat":{"id":-123}}}'
        with patch('urllib.request.urlopen',return_value=Response()):
            self.assertEqual(publish(self.db,self.draft,'dummy','-123'),8)
        event,dev=create_event(self.db,'تکراری','https://www.example.com/item/?utm_campaign=a',['تشبیه','کنایه','تضاد'])
        h=fingerprint(self.sentences,dev)
        with self.db:
            d=self.db.execute('INSERT INTO drafts(event_id,sentences,body_hash,review,status) VALUES(?,?,?,?,?)',(event,json.dumps(self.sentences),h,json.dumps({'approved':True,'body_hash':h,'issues':[]}),'ready')).lastrowid
            self.db.execute('INSERT INTO publication_checks(draft_id,details) VALUES(?,?)',(d,json.dumps({'body_hash':h,'result':'new','url':canonical_url('https://example.com/item'),'history_revision':1})))
        with self.assertRaisesRegex(ValueError,'Duplicate'):publish(self.db,d,'dummy','-123')

    def test_rate_limit_is_deferred(self):
        import io, urllib.error
        error=urllib.error.HTTPError('https://example.com',429,'limited',{},io.BytesIO(b'{"parameters":{"retry_after":60}}'))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'deferred'):publish(self.db,self.draft,'dummy','-123')
        self.assertEqual(self.db.execute('SELECT state FROM deliveries').fetchone()[0],'retry')
        with self.assertRaisesRegex(ValueError,'deferred'):publish(self.db,self.draft,'dummy','-123')

    def test_pending_send_blocks_duplicate_before_network_finishes(self):
        with self.db:
            self.db.execute("INSERT INTO deliveries(draft_id,normalized_url,state) VALUES(?,?,'sending')",(self.draft,canonical_url('https://example.com/item')))
            self.db.execute('UPDATE publication_checks SET details=?',(json.dumps({'body_hash':self.h,'result':'new','url':canonical_url('https://example.com/item'),'history_revision':1}),))
        with patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(ValueError,'Unresolved'):publish(self.db,self.draft,'dummy','-123')
            network.assert_not_called()
