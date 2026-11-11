"""Host-only publication with transactionally reserved deliveries."""
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from .core import canonical_url, fingerprint, validate_sentences


def publish(db, draft_id, token, chat_id):
    if not token or not chat_id: raise ValueError('Separate Telegram credentials required')
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('SELECT d.sentences,d.body_hash,d.review,d.status,e.devices,COALESCE(d.source_url,e.source_url),e.id FROM drafts d JOIN events e ON e.id=d.event_id WHERE d.id=?',(draft_id,)).fetchone()
        if not row: raise ValueError('Draft missing')
        sentences,h,review,status,devices,url,event_id=row
        sentences=json.loads(sentences); devices=json.loads(devices); review=json.loads(review or '{}')
        body=validate_sentences(sentences)
        if status!='ready' or fingerprint(sentences,devices)!=h or review.get('approved') is not True or review.get('issues') != [] or review.get('body_hash')!=h:
            raise ValueError('Draft not approved for this exact text')
        if len((body+'\n\nمنبع: '+url).encode('utf-16-le'))//2 > 4096: raise ValueError('Message exceeds Telegram limit')
        normalized=canonical_url(url)
        check=db.execute('SELECT details FROM publication_checks WHERE draft_id=? ORDER BY id DESC LIMIT 1',(draft_id,)).fetchone()
        if not check: raise ValueError('Fresh duplicate check required')
        details=json.loads(check[0])
        if details.get('body_hash')!=h or details.get('result') not in ('new','update') or details.get('url')!=normalized:
            raise ValueError('Invalid duplicate check')
        last_sent=db.execute("SELECT COALESCE(MAX(id),0) FROM deliveries WHERE state IN ('sent','sending','uncertain')").fetchone()[0]
        if details.get('history_revision') != last_sent: raise ValueError('Stale duplicate check')
        deferred=db.execute("SELECT retry_at FROM deliveries WHERE draft_id=? AND state='retry' ORDER BY id DESC LIMIT 1",(draft_id,)).fetchone()
        if deferred and deferred[0] and datetime.fromisoformat(deferred[0]) > datetime.now(timezone.utc): raise ValueError('Retry deferred')
        prior=db.execute("SELECT dl.state,dl.message_id,d.event_id FROM deliveries dl JOIN drafts d ON d.id=dl.draft_id WHERE dl.normalized_url=? AND dl.state IN ('sent','sending','uncertain')",(normalized,)).fetchall()
        if any(x[0] in ('sending','uncertain') for x in prior): raise ValueError('Unresolved delivery')
        if details['result']=='new' and prior: raise ValueError('Duplicate URL')
        if db.execute("SELECT 1 FROM deliveries dl JOIN drafts d ON d.id=dl.draft_id WHERE d.event_id=? AND dl.state IN ('sending','uncertain')",(event_id,)).fetchone(): raise ValueError('Unresolved related event')
        if details['result']=='new' and db.execute("SELECT 1 FROM deliveries dl JOIN drafts d ON d.id=dl.draft_id WHERE d.event_id=? AND dl.state IN ('sent','sending','uncertain')",(event_id,)).fetchone():
            raise ValueError('Event already published or unresolved')
        parent=details.get('reply_to_message_id')
        if details['result']=='update':
            if not details.get('new_facts') or not parent: raise ValueError('Update requires new facts and original message')
            original=db.execute("SELECT dl.message_id FROM deliveries dl JOIN drafts d ON d.id=dl.draft_id WHERE d.event_id=? AND dl.state='sent' ORDER BY dl.id LIMIT 1",(event_id,)).fetchone()
            if not original or original[0]!=parent: raise ValueError('Update target is not original')
        if db.execute("SELECT 1 FROM deliveries WHERE draft_id=? AND state IN ('sent','sending','uncertain','failed')",(draft_id,)).fetchone():
            raise ValueError('Existing delivery')
        db.execute("INSERT INTO deliveries(draft_id,normalized_url,state) VALUES(?,?,'sending')",(draft_id,normalized))
        delivery=db.execute('SELECT last_insert_rowid()').fetchone()[0];db.commit()
    except Exception:
        db.rollback();raise
    payload={'chat_id':chat_id,'text':body+'\n\nمنبع: '+url,'link_preview_options':{'is_disabled':False,'url':url}}
    if details['result']=='update': payload['reply_parameters']={'message_id':parent,'allow_sending_without_reply':False}
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=25) as response: result=json.load(response)
        if not result.get('ok') or not result.get('result',{}).get('message_id'): raise ValueError('Uncertain response')
        mid=result['result']['message_id']
        if str(result['result'].get('chat',{}).get('id')) != str(chat_id): raise ValueError('Unexpected response chat')
        with db:
            db.execute("UPDATE deliveries SET state='sent',message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(mid,delivery))
            db.execute("UPDATE drafts SET status='published' WHERE id=?",(draft_id,))
        return mid
    except urllib.error.HTTPError as exc:
        try: error=json.loads(exc.read())
        except (ValueError, OSError): error={}
        if exc.code==429 and isinstance(error.get('parameters',{}).get('retry_after'),int):
            retry=(datetime.now(timezone.utc)+timedelta(seconds=error['parameters']['retry_after'])).isoformat()
            with db: db.execute("UPDATE deliveries SET state='retry',retry_at=?,error='rate limited' WHERE id=?",(retry,delivery))
            raise RuntimeError('Rate limited; retry deferred') from None
        if 400<=exc.code<500:
            with db: db.execute("UPDATE deliveries SET state='failed',error='Telegram rejected request' WHERE id=?",(delivery,))
            raise RuntimeError('Telegram rejected request') from None
        with db: db.execute("UPDATE deliveries SET state='uncertain',error='delivery outcome unknown' WHERE id=?",(delivery,))
        raise RuntimeError('Delivery outcome unknown; do not retry') from None
    except Exception:
        with db: db.execute("UPDATE deliveries SET state='uncertain',error='delivery outcome unknown',updated_at=CURRENT_TIMESTAMP WHERE id=?",(delivery,))
        raise RuntimeError('Delivery outcome unknown; do not retry') from None


def check_channel(db, token, configured_chat):
    """Resolve destination and verify channel-admin posting rights without logging secrets."""
    def api(method, args):
        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
            data=json.dumps(args).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as response: data=json.load(response)
            if not data.get('ok'): raise ValueError('Telegram rejected verification')
            return data['result']
        except Exception: raise RuntimeError('Telegram channel verification failed') from None
    me=api('getMe',{});chat=api('getChat',{'chat_id':configured_chat})
    if chat.get('type')!='channel': raise ValueError('Destination must be a channel')
    member=api('getChatMember',{'chat_id':chat['id'],'user_id':me['id']})
    if member.get('status')!='creator' and not (member.get('status')=='administrator' and member.get('can_post_messages')):
        raise ValueError('Bot needs channel posting rights')
    db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    bound=db.execute("SELECT value FROM settings WHERE key='channel_id'").fetchone()
    if bound and bound[0]!=str(chat['id']): raise ValueError('Database bound to a different channel')
    with db:db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('channel_id',?)",(str(chat['id']),))
    return str(chat['id'])


def prepare_delivery(db,draft_id,compare):
    """Record model comparison plus actual history revision; fail closed."""
    row=db.execute('SELECT d.sentences,d.body_hash,COALESCE(d.source_url,e.source_url),e.title FROM drafts d JOIN events e ON e.id=d.event_id WHERE d.id=?',(draft_id,)).fetchone()
    if not row:raise ValueError('Unknown draft')
    sentences,h,url,title=row
    history=[{'draft_id':r[0],'message_id':r[1],'event_id':r[2],'title':r[3],'url':r[4],'sentences':json.loads(r[5])} for r in db.execute("SELECT d.id,l.message_id,e.id,e.title,COALESCE(d.source_url,e.source_url),d.sentences FROM deliveries l JOIN drafts d ON d.id=l.draft_id JOIN events e ON e.id=d.event_id WHERE l.state='sent' ORDER BY l.id")]
    result=compare({'candidate':{'title':title,'url':url,'sentences':json.loads(sentences)},'history':history})
    if result.get('result') not in ('new','update','duplicate','hold') or not isinstance(result.get('reason'),str) or not isinstance(result.get('similar_posts'),list): raise ValueError('Invalid duplicate assessment')
    rev=db.execute("SELECT COALESCE(MAX(id),0) FROM deliveries WHERE state IN ('sent','sending','uncertain')").fetchone()[0]
    details={**result,'body_hash':h,'url':canonical_url(url),'history_revision':rev}
    with db: db.execute('INSERT INTO publication_checks(draft_id,details) VALUES(?,?)',(draft_id,json.dumps(details,ensure_ascii=False)))
    return details
