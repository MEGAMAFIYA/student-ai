"""Student AI virtual photo museum: authenticated API, SQLite metadata, optional Cloudflare R2 media."""
from __future__ import annotations
import base64, hashlib, json, logging, os, re, secrets, sqlite3, time, urllib.parse, uuid
from pathlib import Path
from urllib.parse import urlparse
import webapp_security

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / 'webapp' / 'museum'
DB_PATH = os.getenv('MUSEUM_DB_PATH', str(ROOT / 'museum_data' / 'museum.sqlite3'))
MEDIA_DIR = Path(os.getenv('MUSEUM_MEDIA_DIR', str(ROOT / 'museum_data' / 'photos')))
MAX_PHOTO = 5 * 1024 * 1024
MAX_PHOTOS = 30
R2_ENABLED = False
try:
    import boto3
    from botocore.config import Config
    _aid=os.getenv('R2_ACCOUNT_ID','').strip(); _ak=os.getenv('R2_ACCESS_KEY_ID','').strip(); _sk=os.getenv('R2_SECRET_ACCESS_KEY','').strip(); _bucket=os.getenv('R2_BUCKET','').strip()
    if all((_aid,_ak,_sk,_bucket)):
        _s3=boto3.client('s3', endpoint_url=f'https://{_aid}.r2.cloudflarestorage.com', aws_access_key_id=_ak, aws_secret_access_key=_sk, region_name='auto', config=Config(signature_version='s3v4'))
        R2_ENABLED=True
except Exception as exc:
    log.info('Museum R2 unavailable; local media mode: %s', type(exc).__name__)


def _db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB_PATH, timeout=20); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS museums(id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, guide INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, museum_id TEXT NOT NULL, created REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY, museum_id TEXT NOT NULL, owner_id INTEGER NOT NULL, title TEXT NOT NULL, storage_key TEXT NOT NULL, mime TEXT NOT NULL, created REAL NOT NULL, sort_order INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS presence(museum_id TEXT NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL, x REAL NOT NULL DEFAULT 0, z REAL NOT NULL DEFAULT 2, yaw REAL NOT NULL DEFAULT 0, seen REAL NOT NULL, PRIMARY KEY(museum_id,user_id));
    ''')
    return c


def _json(handler, status, data):
    raw=json.dumps(data, ensure_ascii=False).encode()
    handler.send_response(status); handler.send_header('Content-Type','application/json; charset=utf-8'); handler.send_header('Cache-Control','no-store'); handler.send_header('Content-Length',str(len(raw))); handler.end_headers(); handler.wfile.write(raw)


def _auth(payload, token):
    return webapp_security.verify_telegram_init_data(payload.get('init_data',''), token)


def _museum_access(c, mid, user_id, invite=''):
    m=c.execute('SELECT * FROM museums WHERE id=?',(mid,)).fetchone()
    if not m: return None, False
    if int(m['owner_id'])==int(user_id): return m, True
    row=c.execute('SELECT 1 FROM invites WHERE token=? AND museum_id=? AND active=1',(invite,mid)).fetchone() if invite else None
    return m, bool(row)


def _photo_url(photo_id, init_data='', invite=''): return '/api/museum/photo/'+urllib.parse.quote(photo_id,safe='')+'?init_data='+urllib.parse.quote(init_data)+'&invite='+urllib.parse.quote(invite)


def handle_get(handler, token):
    path=urlparse(handler.path).path
    if path.startswith('/miniapp/museum'):
        rel=path.removeprefix('/miniapp/museum').lstrip('/') or 'index.html'
        if rel not in ('index.html','style.css','app.js'): _json(handler,404,{'ok':False}); return True
        try: body=(WEB_DIR/rel).read_bytes()
        except OSError: _json(handler,404,{'ok':False,'error':'Muzey sahifasi topilmadi'}); return True
        handler.send_response(200); handler.send_header('Content-Type',{'html':'text/html; charset=utf-8','css':'text/css; charset=utf-8','js':'application/javascript; charset=utf-8'}[rel.rsplit('.',1)[-1]]); handler.send_header('Cache-Control','no-cache'); handler.send_header('Content-Length',str(len(body))); handler.end_headers(); handler.wfile.write(body); return True
    if path.startswith('/api/museum/photo/'):
        pid=path.rsplit('/',1)[-1]
        q=urllib.parse.parse_qs(urlparse(handler.path).query); user=_auth({'init_data':q.get('init_data',[''])[0]},token)
        c=_db(); row=c.execute('SELECT p.storage_key,p.mime,p.museum_id FROM photos p WHERE p.id=?',(pid,)).fetchone()
        m,allowed=_museum_access(c,row['museum_id'],user['id'],(q.get('invite') or [''])[0]) if row and user else (None,False)
        c.close()
        if not allowed: handler.send_error(403); return True
        if not row: handler.send_error(404); return True
        key=row['storage_key']
        try:
            if key.startswith('r2:'):
                obj=_s3.get_object(Bucket=_bucket,Key=key[3:]); data=obj['Body'].read()
            else: data=Path(key).read_bytes()
            handler.send_response(200); handler.send_header('Content-Type',row['mime']); handler.send_header('Cache-Control','public, max-age=3600'); handler.send_header('Content-Length',str(len(data))); handler.end_headers(); handler.wfile.write(data)
        except Exception: handler.send_error(404)
        return True
    if path=='/api/museum/mine':
        q=urllib.parse.parse_qs(urlparse(handler.path).query); user=_auth({'init_data':q.get('init_data',[''])[0]},token)
        if not user: _json(handler,403,{'ok':False,'error':'Telegram tasdiqlashi yaroqsiz'}); return True
        c=_db(); m=c.execute('SELECT id,title FROM museums WHERE owner_id=? ORDER BY created DESC LIMIT 1',(int(user['id']),)).fetchone(); c.close()
        _json(handler,200,{'ok':True,'museum':dict(m) if m else None}); return True
    if path=='/api/museum/state':
        q=urllib.parse.parse_qs(urlparse(handler.path).query); mid=(q.get('museum') or [''])[0]; invite=(q.get('invite') or [''])[0]
        user=_auth({'init_data':q.get('init_data',[''])[0]},token)
        if not user: _json(handler,403,{'ok':False,'error':'Telegram tasdiqlashi yaroqsiz'}); return True
        c=_db(); m,allowed=_museum_access(c,mid,user['id'],invite)
        if not allowed: c.close(); _json(handler,403,{'ok':False,'error':'Bu muzeyga kirish uchun taklif havolasi kerak'}); return True
        now=time.time(); c.execute('DELETE FROM presence WHERE seen<?',(now-20,))
        c.execute('INSERT INTO presence(museum_id,user_id,name,x,z,yaw,seen) VALUES(?,?,?,?,?,?,?) ON CONFLICT(museum_id,user_id) DO UPDATE SET name=excluded.name,seen=excluded.seen',(mid,user['id'],(user.get('first_name') or 'Mehmon')[:48],0,2,0,now))
        photos=[dict(r) for r in c.execute('SELECT id,title,sort_order FROM photos WHERE museum_id=? ORDER BY sort_order',(mid,)).fetchall()]
        people=[dict(r) for r in c.execute('SELECT user_id,name,x,z,yaw FROM presence WHERE museum_id=? AND seen>?',(mid,now-12)).fetchall()]
        c.commit(); c.close()
        for p in photos: p['url']=_photo_url(p.pop('id'), q.get('init_data',[''])[0], invite)
        _json(handler,200,{'ok':True,'museum':{'id':mid,'title':m['title'],'owner_id':m['owner_id'],'guide':bool(m['guide'])},'photos':photos,'people':people,'is_owner':int(m['owner_id'])==int(user['id']),'r2_enabled':R2_ENABLED})
        return True
    return False


def handle_post(handler, token):
    if not handler.path.startswith('/api/museum/'): return False
    try: length=int(handler.headers.get('Content-Length','0'))
    except ValueError: length=0
    if length<=0 or length>MAX_PHOTO*2+100_000: _json(handler,413,{'ok':False,'error':'So‘rov hajmi juda katta'}); return True
    try: payload=json.loads(handler.rfile.read(length).decode('utf-8'))
    except Exception: _json(handler,400,{'ok':False,'error':'JSON xato'}); return True
    user=_auth(payload,token)
    if not user: _json(handler,403,{'ok':False,'error':'Telegram tasdiqlashi yaroqsiz'}); return True
    uid=int(user['id']); action=urlparse(handler.path).path.rsplit('/',1)[-1]
    c=_db(); now=time.time()
    try:
        if action=='create':
            title=str(payload.get('title') or 'Mening xotiralarim muzeyi').strip()[:80]
            mid=uuid.uuid4().hex[:16]; c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)',(mid,uid,title,now)); c.commit(); _json(handler,200,{'ok':True,'museum':mid})
        elif action=='invite':
            mid=str(payload.get('museum') or ''); m=c.execute('SELECT owner_id FROM museums WHERE id=?',(mid,)).fetchone()
            if not m or int(m['owner_id'])!=uid: _json(handler,403,{'ok':False,'error':'Faqat muzey egasi taklif yarata oladi'})
            else:
                invite=secrets.token_hex(18); c.execute('INSERT INTO invites(token,museum_id,created) VALUES(?,?,?)',(invite,mid,now)); c.commit(); _json(handler,200,{'ok':True,'invite':invite})
        elif action=='photo':
            mid=str(payload.get('museum') or ''); m=c.execute('SELECT owner_id FROM museums WHERE id=?',(mid,)).fetchone()
            if not m or int(m['owner_id'])!=uid: _json(handler,403,{'ok':False,'error':'Faqat egasi surat yuklaydi'})
            else:
                count=c.execute('SELECT COUNT(*) n FROM photos WHERE museum_id=?',(mid,)).fetchone()['n']
                data=str(payload.get('image') or ''); head,sep,b64=data.partition(','); raw=base64.b64decode(b64,validate=True) if sep else b''
                mime='image/png' if 'image/png' in head else 'image/jpeg' if 'image/jpeg' in head else ''
                if count>=MAX_PHOTOS or len(raw)>MAX_PHOTO or not mime or not (raw.startswith(b'\x89PNG\r\n\x1a\n') if mime=='image/png' else raw.startswith(b'\xff\xd8\xff')): _json(handler,400,{'ok':False,'error':'Rasm PNG/JPEG bo‘lsin, 5 MB dan kichik va 30 tadan oshmasin'})
                else:
                    pid=uuid.uuid4().hex; ext='.png' if mime=='image/png' else '.jpg'; key=f'museum/{mid}/{pid}{ext}'
                    if R2_ENABLED:
                        _s3.put_object(Bucket=_bucket,Key=key,Body=raw,ContentType=mime); store='r2:'+key
                    else:
                        MEDIA_DIR.mkdir(parents=True,exist_ok=True); dest=MEDIA_DIR/f'{pid}{ext}'; dest.write_bytes(raw); store=str(dest)
                    title=str(payload.get('title') or 'Xotira').strip()[:100]
                    c.execute('INSERT INTO photos(id,museum_id,owner_id,title,storage_key,mime,created,sort_order) VALUES(?,?,?,?,?,?,?,?)',(pid,mid,uid,title,store,mime,now,count)); c.commit(); _json(handler,200,{'ok':True,'photo':pid,'r2_enabled':R2_ENABLED})
        elif action=='presence':
            mid=str(payload.get('museum') or ''); invite=str(payload.get('invite') or ''); m,allowed=_museum_access(c,mid,uid,invite)
            if not allowed: _json(handler,403,{'ok':False,'error':'Muzeyga ruxsat yo‘q'})
            else:
                name=str(user.get('first_name') or 'Mehmon')[:48]; x=max(-5,min(5,float(payload.get('x',0)))); z=max(-5,min(5,float(payload.get('z',2)))); yaw=float(payload.get('yaw',0))
                c.execute('INSERT INTO presence(museum_id,user_id,name,x,z,yaw,seen) VALUES(?,?,?,?,?,?,?) ON CONFLICT(museum_id,user_id) DO UPDATE SET name=excluded.name,x=excluded.x,z=excluded.z,yaw=excluded.yaw,seen=excluded.seen',(mid,uid,name,x,z,yaw,now)); c.commit(); _json(handler,200,{'ok':True})
        elif action=='guide':
            mid=str(payload.get('museum') or ''); m=c.execute('SELECT owner_id FROM museums WHERE id=?',(mid,)).fetchone()
            if not m or int(m['owner_id'])!=uid: _json(handler,403,{'ok':False,'error':'Faqat egasi yo‘naltirishni boshqaradi'})
            else: c.execute('UPDATE museums SET guide=? WHERE id=?',(1 if payload.get('enabled') else 0,mid)); c.commit(); _json(handler,200,{'ok':True})
        else: _json(handler,404,{'ok':False,'error':'Noma’lum amal'})
    except Exception as exc:
        c.rollback(); log.exception('Museum API error'); _json(handler,500,{'ok':False,'error':'Serverda xatolik'})
    finally: c.close()
    return True


def museum_command_url(base_url: str) -> str:
    return base_url.rstrip('/')+'/miniapp/museum/' if base_url else ''
