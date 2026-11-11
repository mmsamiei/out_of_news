import json
import sqlite3
import unittest
from outnews.core import canonical_url, create_event, fingerprint, initialize, validate_sentences


class CoreTests(unittest.TestCase):
    def test_url_normalization_preserves_semantic_query(self):
        self.assertEqual(canonical_url('http://www.example.org/story/?utm_source=x&id=3#part'),
                         canonical_url('https://example.org/story?id=3'))
        self.assertEqual(canonical_url('https://bbc.com/a?at_medium=RSS&at_campaign=rss'), canonical_url('https://bbc.com/a'))
        self.assertNotEqual(canonical_url('https://example.org/?id=3'), canonical_url('https://example.org/?id=4'))

    def test_selection_persists(self):
        db = sqlite3.connect(':memory:'); initialize(db)
        i, devices = create_event(db, 'عنوان', 'https://example.org/a', ['تشبیه', 'کنایه', 'تضاد', 'استعاره'])
        self.assertIn(len(devices), (2, 3)); self.assertEqual(len(devices), len(set(devices)))
        self.assertEqual(json.loads(db.execute('select devices from events where id=?', (i,)).fetchone()[0]), devices)

    def test_sentence_validation(self):
        s = ['این یک خبر تازه است.', 'شرکت مدل 2.6 را معرفی کرد.', 'کارشناسان نتیجه را بررسی می‌کنند.']
        self.assertTrue(validate_sentences(s))
        for bad in [s[:2], ['این خوب است. این بد است.', s[1], s[2]], ['This is English.', 'Another sentence.', 'Third sentence.']]:
            with self.assertRaises(ValueError): validate_sentences(bad)
        self.assertNotEqual(fingerprint(s, ['تشبیه']), fingerprint(s, ['کنایه']))
