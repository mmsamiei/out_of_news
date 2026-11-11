"""Durable literary selection and conservative publication validation."""
import hashlib
import json
import re
import secrets
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

PERSIAN = re.compile(r'[\u0621-\u063a\u0641-\u064a\u0671-\u06d3]')
LATIN = re.compile(r'[A-Za-z]')


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid source URL')
    host = p.hostname.lower().removeprefix('www.')
    port = p.port
    if port and port not in (80, 443):
        host += ':' + str(port)
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid', 'at_medium', 'at_campaign', 'mc_cid', 'mc_eid'))
    return urlunsplit(('https', host, p.path.rstrip('/') or '/', urlencode(query), ''))


def initialize(db):
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
        source_url TEXT NOT NULL, normalized_url TEXT NOT NULL, devices TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS drafts(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id),
        sentences TEXT NOT NULL, body_hash TEXT NOT NULL, review TEXT, source_url TEXT, status TEXT NOT NULL DEFAULT 'hold');
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES drafts(id),
        normalized_url TEXT NOT NULL, state TEXT NOT NULL, message_id INTEGER, error TEXT, retry_at TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS publication_checks(id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES drafts(id),
        details TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    ''')
    if 'source_url' not in {r[1] for r in db.execute('PRAGMA table_info(drafts)')}:
        db.execute('ALTER TABLE drafts ADD COLUMN source_url TEXT')
    db.commit()


def create_event(db, title, source_url, catalog):
    if len(set(catalog)) < 3:
        raise ValueError('At least three unique devices required')
    count = 2 + secrets.randbelow(2)
    pool = list(dict.fromkeys(catalog))
    selected = []
    for _ in range(count):
        selected.append(pool.pop(secrets.randbelow(len(pool))))
    with db:
        cur = db.execute('INSERT INTO events(title,source_url,normalized_url,devices) VALUES(?,?,?,?)',
                         (title, source_url, canonical_url(source_url), json.dumps(selected, ensure_ascii=False)))
    return cur.lastrowid, selected


def validate_sentences(sentences):
    if not isinstance(sentences, list) or len(sentences) != 3:
        raise ValueError('Exactly three sentences required')
    for s in sentences:
        if not isinstance(s, str) or not s.strip() or '\n' in s or not re.search(r'[.!؟?…][»”"\']?$', s.strip()):
            raise ValueError('Each entry must be one complete sentence')
        if re.search(r'[؟?!…]|(?<!\d)\.(?!\d)', s.strip()[:-1]):
            raise ValueError('Multiple sentence endings in entry')
    body = ' '.join(s.strip() for s in sentences)
    if re.search(r'https?://', body, flags=re.I):
        raise ValueError('Links must be in the source footer only')
    if not PERSIAN.match(body) or len(PERSIAN.findall(body)) < len(LATIN.findall(body)):
        raise ValueError('Persian body required')
    return body


def fingerprint(sentences, devices):
    payload = json.dumps({'sentences': sentences, 'devices': devices}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
