"""Scheduled host controller; no public delivery without explicit activation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from .agent import call
from .core import canonical_url, create_event, fingerprint, initialize, validate_sentences
from .sources import FEEDS, fetch_feed
from .fetch import fetch_article

ROOT=Path(__file__).resolve().parent.parent
DISCOVERY=['https://farsnews.ir','https://apnews.com/world-news','https://apnews.com/business','https://apnews.com/science','https://apnews.com/technology','https://apnews.com/entertainment','https://apnews.com/sports','https://www.reuters.com/world/','https://www.reuters.com/business/','https://www.reuters.com/technology/','https://www.reuters.com/lifestyle/','https://www.reuters.com/sports/']


def process_writing_jobs(db,deadline):
    completed=[]
    jobs=db.execute("SELECT j.id,j.event_id,j.article,j.source_text,j.attempts,e.devices FROM writing_jobs j JOIN events e ON e.id=j.event_id WHERE j.status='pending' ORDER BY j.event_id").fetchall()
    for job,event,raw,source_text,attempts,raw_devices in jobs:
        if time.monotonic()>deadline-30:break
        item=json.loads(raw);devices=json.loads(raw_devices)
        while attempts<3 and time.monotonic()<deadline-30:
            attempts+=1
            with db:db.execute('UPDATE writing_jobs SET attempts=? WHERE id=?',(attempts,job))
            try:
                data=call('Write exactly three complete conversational Persian sentences, using all assigned devices. Preserve facts and attribution. Return JSON {"sentences":[...],"evidence":"..."} or {"hold":"reason"}.',{'article':item,'source_text':source_text,'devices':devices,'definitions':(ROOT/'docs/literary-devices-proposal.md').read_text()},timeout=min(120,max(1,int(deadline-time.monotonic()))))
                if data.get('hold'):
                    with db:db.execute("UPDATE writing_jobs SET status='hold',error=? WHERE id=?",(str(data['hold'])[:500],job))
                    break
                sentences=data.get('sentences');body=validate_sentences(sentences);h=fingerprint(sentences,devices)
                review=call('Review against supplied source text for factual accuracy, exact literary devices with quotes, flow, consistently colloquial Persian (reject formal forms such as «می‌کوشد»، «می‌شود»، «را» or «باشد» when not part of names or quotations), and insulting implications toward religion, government and leaders; criticism alone allowed. Return JSON {"approved":boolean,"body_hash":"...","devices":[{"name":"...","quote":"...","reason":"..."}],"issues":[]}.',{'article':item,'source_text':source_text,'devices':devices,'definitions':(ROOT/'docs/literary-devices-proposal.md').read_text(),'sentences':sentences,'body_hash':h},timeout=min(120,max(1,int(deadline-time.monotonic()))))
                ok=review.get('approved') is True and review.get('issues') == [] and review.get('body_hash')==h and {x.get('name') for x in review.get('devices',[]) if x.get('quote') and x['quote'] in body}==set(devices)
                with db:
                    db.execute('INSERT INTO drafts(event_id,sentences,body_hash,review,source_url,status) VALUES(?,?,?,?,?,?)',(event,json.dumps(sentences,ensure_ascii=False),h,json.dumps(review,ensure_ascii=False),item['url'],'ready' if ok else 'hold'))
                    if ok:db.execute("UPDATE writing_jobs SET status='ready' WHERE id=?",(job,))
                if ok:break
            except (ValueError,RuntimeError,KeyError,TypeError) as exc:
                with db:db.execute('UPDATE writing_jobs SET error=? WHERE id=?',(type(exc).__name__,job))
        if attempts>=3:
            with db:db.execute("UPDATE writing_jobs SET status='hold' WHERE id=? AND status='pending'",(job,))
        completed.append(event)
    return completed


def run_cycle(db,deadline):
    db.execute('CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,source TEXT,url TEXT,title TEXT,published TEXT,observed_at TEXT DEFAULT CURRENT_TIMESTAMP,content TEXT,digest TEXT,status TEXT,UNIQUE(source,url,published,digest))')
    db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS candidate_decisions(id INTEGER PRIMARY KEY,observed_at TEXT DEFAULT CURRENT_TIMESTAMP,details TEXT NOT NULL)')
    db.execute("CREATE TABLE IF NOT EXISTS writing_jobs(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL REFERENCES events(id),article TEXT NOT NULL UNIQUE,source_text TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',error TEXT)")
    db.execute('CREATE TABLE IF NOT EXISTS source_checks(id INTEGER PRIMARY KEY,source TEXT,checked_at TEXT DEFAULT CURRENT_TIMESTAMP,count INTEGER,error TEXT,details TEXT)')
    db.commit()
    start_checks=db.execute('SELECT COALESCE(MAX(id),0) FROM source_checks').fetchone()[0]
    start_decisions=db.execute('SELECT COALESCE(MAX(id),0) FROM candidate_decisions').fetchone()[0]
    process_writing_jobs(db,deadline); candidates=[]
    if time.monotonic()>=deadline-30:return {'events':[],'web_source_checks':[],'deferred':'time budget'}
    def get(pair):
        source,url=pair
        try:return source,fetch_feed(url),None
        except Exception as e:return source,[],type(e).__name__
    with ThreadPoolExecutor(max_workers=5) as pool:
        for source,items,error in pool.map(get,FEEDS.items()):
            with db:
                db.execute('INSERT INTO source_checks(source,count,error) VALUES(?,?,?)',(source,len(items),error))
                for item in items:
                    db.execute('INSERT OR IGNORE INTO observations(source,url,title,published,content,digest,status) VALUES(?,?,?,?,?,?,?)',(source,item['url'],item['title'],item['published'],item['content'],hashlib.sha256(item['content'].encode()).hexdigest(),'pending'))
    candidates=[dict(zip(('id','source','url','title','published','content'),r)) for r in db.execute("SELECT id,source,url,title,published,content FROM observations WHERE status='pending' ORDER BY published DESC")]
    history=[dict(zip(('id','title','url','facts'),r)) for r in db.execute("SELECT e.id,e.title,e.source_url,group_concat(d.sentences) FROM events e JOIN drafts d ON d.event_id=e.id JOIN deliveries l ON l.draft_id=d.id WHERE l.state='sent' GROUP BY e.id")]
    # Keep each model input bounded and persist observations before inference.
    # Failed batches stay pending and do not block other sources.
    result={'candidates':[],'source_checks':[]}
    saved=db.execute("SELECT value FROM settings WHERE key='discovery_cursor'").fetchone()
    cursor=int(saved[0]) % len(candidates) if saved and candidates else 0
    ordered=candidates[cursor:]+candidates[:cursor]
    batches=[([],DISCOVERY)] + [(ordered[i:i+25],[]) for i in range(0,len(ordered),25)]
    discovery_deadline=min(deadline-90,time.monotonic()+240)
    for batch,web in batches:
        if time.monotonic()>discovery_deadline:break
        try:
            found=call('Inspect the supplied web listings and search their domains if blocked. Evaluate the supplied RSS candidates. Return only important recent distinct stories in JSON {"candidates":[{"id":input id or null,"title":"...","url":"...","published":"ISO timestamp","content":"source facts","decision":"new|update|duplicate|hold","reason":"...","similar_event_id":integer or null,"new_facts":[]}],"source_checks":[]}. Group incoming reports of the same story and select one as new when no prior published event exists. Duplicate means a matching prior event in history, not another input candidate. Do not invent evidence. Do not claim inaccessible sources were checked.',{'rss':batch,'web_sources':web,'history':history},timeout=min(60,max(1,int(discovery_deadline-time.monotonic()))))
            if not isinstance(found.get('candidates'),list) or not isinstance(found.get('source_checks'),list):raise ValueError('Invalid discovery output')
            result['candidates'].extend(found['candidates']);result['source_checks'].extend(found['source_checks'])
        except (RuntimeError,ValueError) as exc:
            result['source_checks'].append({'source':'discovery_batch','error':type(exc).__name__,'candidate_ids':[x['id'] for x in batch],'web_sources':web})
        finally:
            if batch and candidates:
                cursor=(cursor+len(batch)) % len(candidates)
                with db:db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('discovery_cursor',?)",(str(cursor),))
    with db:
        for check in result.get('source_checks',[]):
            db.execute('INSERT INTO source_checks(source,count,error,details) VALUES(?,?,?,?)',(str(check.get('source') or check.get('url') or 'unknown'),check.get('count'),check.get('error'),json.dumps(check,ensure_ascii=False)))
        for candidate in result.get('candidates',[]):
            db.execute('INSERT INTO candidate_decisions(details) VALUES(?)',(json.dumps(candidate,ensure_ascii=False),))
    catalog=json.loads((ROOT/'config/literary-devices.json').read_text()); output=[]
    for item in result.get('candidates',[]):
        if time.monotonic()>deadline-30: break
        try:
            url=canonical_url(item['url']);oid=item.get('id');decision=item.get('decision')
            if oid:
                observed=next((c for c in candidates if c['id']==oid),None)
                if not observed or canonical_url(observed['url'])!=url:continue
                item['published']=observed['published']
                item['source']=observed['source']
            if decision not in ('new','update','duplicate','hold'):continue
            if decision in ('duplicate','hold'):
                if oid:
                    with db:db.execute('UPDATE observations SET status=? WHERE id=?',(decision,oid))
                continue
            if not item.get('content') or not item.get('published'):continue
            try:
                published=datetime.fromisoformat(item['published'].replace('Z','+00:00'))
                if published.tzinfo is None:continue
                if published < datetime.now(timezone.utc)-timedelta(hours=24) and decision!='update':continue
            except (ValueError,AttributeError):continue
            try: source_text=fetch_article(item['url'])
            except Exception:
                if oid:
                    with db:db.execute("UPDATE observations SET status='hold' WHERE id=?",(oid,))
                continue
            if decision=='new':
                existing=db.execute('SELECT id,devices FROM events WHERE normalized_url=? ORDER BY id LIMIT 1',(url,)).fetchone()
                if existing:
                    event=existing[0];devices=json.loads(existing[1])
                    if db.execute('SELECT 1 FROM writing_jobs WHERE event_id=?',(event,)).fetchone():continue
                    if db.execute("SELECT 1 FROM deliveries dl JOIN drafts d ON d.id=dl.draft_id WHERE d.event_id=? AND dl.state IN ('sent','sending','uncertain')",(event,)).fetchone():continue
                else:
                    event,devices=create_event(db,item['title'],item['url'],catalog)
            else:
                event=item.get('similar_event_id')
                existing=db.execute('SELECT devices FROM events WHERE id=?',(event,)).fetchone()
                if not existing or not item.get('new_facts'):continue
                devices=json.loads(existing[0])
            with db:db.execute('INSERT OR IGNORE INTO writing_jobs(event_id,article,source_text) VALUES(?,?,?)',(event,json.dumps(item,ensure_ascii=False),source_text))
            process_writing_jobs(db,deadline)
            if oid:
                with db:db.execute("UPDATE observations SET status='processed' WHERE id=?",(oid,))
            output.append({'event_id':event,'url':url,'devices':devices})
        except (KeyError,ValueError,RuntimeError) as e:output.append({'error':type(e).__name__})
    checks=[{'source':r[0],'count':r[1],'error':r[2],'details':json.loads(r[3]) if r[3] else None} for r in db.execute('SELECT source,count,error,details FROM source_checks WHERE id>? ORDER BY id',(start_checks,))]
    decisions=[json.loads(r[0]) for r in db.execute('SELECT details FROM candidate_decisions WHERE id>? ORDER BY id',(start_decisions,))]
    queue=dict(db.execute('SELECT status,count(*) FROM drafts GROUP BY status').fetchall())
    return {'events':output,'source_checks':checks,'decisions':decisions,'drafts_by_status':queue}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--database',default=os.environ.get('OUT_OF_NEWS_DATABASE_PATH',str(ROOT/'data'/'news.sqlite3')));ap.add_argument('--publish',action='store_true');args=ap.parse_args()
    if args.publish and os.environ.get('OUT_OF_NEWS_ENABLE_LIVE') != '1': raise SystemExit('Live mode requires explicit activation')
    data=Path(args.database);data.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with open(data.parent/'cycle.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: print('cycle already running');return
        db=sqlite3.connect(data);initialize(db)
        with db: db.execute("UPDATE deliveries SET state='uncertain',error='interrupted before confirmation' WHERE state='sending'")
        deadline=time.monotonic()+720
        try: result=run_cycle(db,deadline)
        except (RuntimeError,ValueError,OSError) as exc: result={'events':[],'cycle_error':type(exc).__name__}
        result['publication']=[]
        if args.publish:
            from .telegram import check_channel, prepare_delivery, publish
            token=os.environ.get('TELEGRAM_BOT_TOKEN',''); target=os.environ.get('TELEGRAM_CHAT_ID','')
            channel=check_channel(db,token,target)
            for (draft_id,) in db.execute("SELECT id FROM drafts WHERE status='ready' ORDER BY id").fetchall():
                if time.monotonic()>=deadline-30: break
                def compare(payload):
                    return call('Compare this candidate with ALL previous posts by fact, names and date, even when URLs differ. Return JSON {"result":"new|update|duplicate|hold","reason":"...","similar_posts":[],"new_facts":[],"reply_to_message_id":null}. If uncertain hold.',payload,timeout=min(120,int(deadline-time.monotonic())))
                try:
                    check=prepare_delivery(db,draft_id,compare)
                    if check['result'] in ('new','update'):
                        mid=publish(db,draft_id,token,channel);result['publication'].append({'draft_id':draft_id,'message_id':mid})
                except (RuntimeError,ValueError) as exc:
                    result['publication'].append({'draft_id':draft_id,'error':str(exc)})
                    if 'unknown' in str(exc) or 'deferred' in str(exc):break
        out=ROOT/'output';out.mkdir(mode=0o700,exist_ok=True)
        p=out/('cycle-'+str(int(time.time()))+'.json');p.write_text(json.dumps(result,ensure_ascii=False,indent=2));p.chmod(0o600);print(p)

if __name__=='__main__':main()
