"""Conservative allowlisted HTTPS article extraction."""
from html.parser import HTMLParser
from ipaddress import ip_address
import socket
from urllib.parse import urlsplit
import urllib.request

ALLOWED=('isna.ir','irna.ir','farsnews.ir','bbc.com','bbc.co.uk','apnews.com','reuters.com')


def validate_url(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not any(host==x or host.endswith('.'+x) for x in ALLOWED):
        raise ValueError('Article URL is not allowlisted HTTPS')
    addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Non-public destination')
    return url


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Text(HTMLParser):
    def __init__(self):super().__init__();self.ignored=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'):self.ignored+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg') and self.ignored:self.ignored-=1
    def handle_data(self,data):
        if not self.ignored and data.strip():self.parts.append(data.strip())


def fetch_article(url):
    validate_url(url)
    opener=urllib.request.build_opener(Redirects())
    req=urllib.request.Request(url,headers={'User-Agent':'OutOfNews/1.0'})
    with opener.open(req,timeout=20) as response:
        if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('Non-HTML article')
        data=response.read(2_000_001)
    if len(data)>2_000_000:raise ValueError('Article too large')
    parser=Text();parser.feed(data.decode('utf-8',errors='replace'));text='\n'.join(parser.parts)
    if len(text)<500:raise ValueError('Insufficient article text')
    return text[:100000]
