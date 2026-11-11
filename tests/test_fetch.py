import unittest
from unittest.mock import patch
from outnews.fetch import validate_url

class FetchTests(unittest.TestCase):
    def test_private_and_forged_hosts_blocked(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):validate_url('https://www.bbc.com/news/a')
        for url in ('https://bbc.com.attacker.example/news','http://www.bbc.com/news','https://bbc.com@attacker.example/a','https://127.0.0.1/a'):
            with self.assertRaises(ValueError):validate_url(url)
