"""RSS discovery. URLs from articles remain untrusted."""
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = {
 'isna':'https://www.isna.ir/rss', 'irna':'https://www.irna.ir/rss',
 'bbc_world':'https://feeds.bbci.co.uk/news/world/rss.xml',
 'bbc_science':'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml',
 'bbc_technology':'https://feeds.bbci.co.uk/news/technology/rss.xml',
}


def fetch_feed(url):
    req=urllib.request.Request(url,headers={'User-Agent':'OutOfNews/1.0'})
    with urllib.request.urlopen(req,timeout=20) as r: body=r.read(4_000_001)
    if len(body)>4_000_000: raise ValueError('feed too large')
    root=ET.fromstring(body); now=datetime.now(timezone.utc); items=[]
    for item in root.findall('./channel/item'):
        title=(item.findtext('title') or '').strip(); url=(item.findtext('link') or '').strip()
        raw=item.findtext('pubDate') or ''
        try:
            published=parsedate_to_datetime(raw)
            if published.tzinfo is None: continue
            published=published.astimezone(timezone.utc)
        except (ValueError,TypeError,OverflowError): continue
        if published > now + timedelta(minutes=5): continue
        if title and url.startswith(('https://','http://')):
            items.append({'title':title,'url':url,'published':published.isoformat(),
                          'content':(item.findtext('description') or '')[:12000]})
    return items
