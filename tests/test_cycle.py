import sqlite3
import time
import unittest
from unittest.mock import patch
from outnews.core import initialize
from outnews.cycle import run_cycle

class CycleTests(unittest.TestCase):
    def test_collection_and_review_create_ready_draft(self):
        db=sqlite3.connect(':memory:');initialize(db)
        item={'title':'خبر آزمون','url':'https://example.org/a','published':'2026-11-10T12:00:00+00:00','content':'متن معتبر خبر'}
        def model(task,payload,timeout):
            if 'Inspect the supplied web listings' in task:
                if not payload['rss']:return {'candidates':[],'source_checks':[]}
                return {'candidates':[dict(item,id=payload['rss'][0]['id'],decision='new',reason='distinct')],'source_checks':[]}
            if 'Write exactly' in task:
                devices=payload['devices'];return {'sentences':['این خبر '+devices[0]+' را مطرح کرد.','این موضوع '+devices[1]+' را نشان داد.','این گزارش '+(devices[2] if len(devices)>2 else 'دیگر')+' را بررسی کرد.'],'evidence':'متن معتبر خبر'}
            return {'approved':True,'body_hash':payload['body_hash'],'devices':[{'name':d,'quote':d} for d in payload['devices']],'issues':[]}
        with patch('outnews.cycle.FEEDS',{'example':'https://example.org/rss'}),patch('outnews.cycle.fetch_feed',return_value=[item]),patch('outnews.cycle.fetch_article',return_value='متن خبر '*100),patch('outnews.cycle.call',side_effect=model):
            result=run_cycle(db,time.monotonic()+300)
        self.assertEqual(db.execute("select status from drafts").fetchone()[0],'ready')
        self.assertEqual(len(result['events']),1)
        self.assertEqual(db.execute('select count(*) from deliveries').fetchone()[0],0)

    def test_pending_job_reuses_event_after_interruption(self):
        from outnews.cycle import process_writing_jobs
        from outnews.core import create_event
        import json
        db=sqlite3.connect(':memory:');initialize(db)
        db.execute("CREATE TABLE writing_jobs(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL,article TEXT NOT NULL UNIQUE,source_text TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',error TEXT)")
        event,devices=create_event(db,'خبر','https://example.org/a',['تشبیه','تضاد','کنایه'])
        db.execute('INSERT INTO writing_jobs(event_id,article,source_text,attempts) VALUES(?,?,?,?)',(event,json.dumps({'url':'https://example.org/a'}),'متن منبع',1));db.commit()
        with patch('outnews.cycle.call',return_value={'hold':'source needs review'}):process_writing_jobs(db,time.monotonic()+300)
        self.assertEqual(db.execute('select attempts,status from writing_jobs').fetchone(),(2,'hold'))
        self.assertEqual(json.loads(db.execute('select devices from events').fetchone()[0]),devices)
