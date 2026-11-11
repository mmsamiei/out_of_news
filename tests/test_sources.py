import io
import unittest
from unittest.mock import patch
from outnews.sources import fetch_feed

class SourceTests(unittest.TestCase):
    def test_bad_xml_is_not_silently_accepted(self):
        fake=io.BytesIO(b'<rss><broken>')
        with patch('urllib.request.urlopen',return_value=fake):
            with self.assertRaises(Exception):fetch_feed('https://example.org/rss')

    def test_unknown_date_is_not_a_fresh_story(self):
        fake=io.BytesIO(b'<rss><channel><item><title>News</title><link>https://example.org/a</link><pubDate>bad</pubDate></item></channel></rss>')
        with patch('urllib.request.urlopen',return_value=fake):self.assertEqual(fetch_feed('https://example.org/rss'),[])
