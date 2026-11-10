import base64, hashlib, hmac, json, os, tempfile, time
from urllib.parse import urlencode

import museum

class FakeHandler:
    def __init__(self, path, payload=None, token='test-token'):
        self.path=path; self.payload=payload or {}; self.token=token
        self.headers={'Content-Length':str(len(json.dumps(self.payload).encode()))}
        self.rfile=__import__('io').BytesIO(json.dumps(self.payload).encode())
        self.wfile=__import__('io').BytesIO(); self.status=None; self.headers_out={}
    def send_response(self,n): self.status=n
    def send_header(self,k,v): self.headers_out[k]=v
    def end_headers(self): pass
    def send_error(self,n): self.status=n

def signed_init(uid=123):
    user=json.dumps({'id':uid,'first_name':'Test'},separators=(',',':'))
    fields={'auth_date':str(int(time.time())),'query_id':'abc','user':user}
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret=hmac.new(b'WebAppData',b'test-token',hashlib.sha256).digest()
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

def test_create_museum_and_issue_invite(tmp_path, monkeypatch):
    monkeypatch.setattr(museum,'DB_PATH',str(tmp_path/'museum.sqlite3'))
    monkeypatch.setattr(museum,'MEDIA_DIR',tmp_path/'photos')
    h=FakeHandler('/api/museum/create',{'init_data':signed_init(),'title':'Oilaviy xotiralar'})
    assert museum.handle_post(h,'test-token') and h.status==200
    created=json.loads(h.wfile.getvalue())['museum']
    h=FakeHandler('/api/museum/invite',{'init_data':signed_init(),'museum':created})
    museum.handle_post(h,'test-token'); result=json.loads(h.wfile.getvalue())
    assert h.status==200 and len(result['invite'])>=64

def test_photo_requires_owner_and_valid_image(tmp_path, monkeypatch):
    monkeypatch.setattr(museum,'DB_PATH',str(tmp_path/'museum.sqlite3'))
    monkeypatch.setattr(museum,'MEDIA_DIR',tmp_path/'photos')
    c=museum._db(); c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)',('abc',123,'test',time.time())); c.commit(); c.close()
    from PIL import Image
    import io
    buf=io.BytesIO(); Image.new('RGB',(2,2),'red').save(buf,format='PNG'); png=buf.getvalue()
    payload={'init_data':signed_init(),'museum':'abc','title':'test.png','image':'data:image/png;base64,'+base64.b64encode(png).decode()}
    h=FakeHandler('/api/museum/photo',payload); museum.handle_post(h,'test-token')
    assert h.status==200 and json.loads(h.wfile.getvalue())['ok']
    h=FakeHandler('/api/museum/photo',dict(payload,init_data=signed_init(456))); museum.handle_post(h,'test-token')
    assert h.status==403


def test_expired_invite_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(museum,'DB_PATH',str(tmp_path/'museum.sqlite3'))
    c=museum._db()
    c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)',('m1',123,'test',time.time()))
    c.execute('INSERT INTO invites(token,museum_id,created,expires) VALUES(?,?,?,?)',('expired-token','m1',time.time()-100,time.time()-1))
    c.commit()
    m,allowed=museum._museum_access(c,'m1',456,'expired-token')
    c.close()
    assert m is not None and not allowed


def test_revoking_invites_disables_guest_access(tmp_path, monkeypatch):
    monkeypatch.setattr(museum,'DB_PATH',str(tmp_path/'museum.sqlite3'))
    c=museum._db()
    c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)',('m1',123,'test',time.time()))
    c.execute('INSERT INTO invites(token,museum_id,created,expires) VALUES(?,?,?,?)',('active-token','m1',time.time(),time.time()+3600))
    c.commit(); c.close()
    h=FakeHandler('/api/museum/revoke_invite',{'init_data':signed_init(123),'museum':'m1'})
    museum.handle_post(h,'test-token')
    assert h.status==200
    c=museum._db()
    m,allowed=museum._museum_access(c,'m1',456,'active-token')
    c.close()
    assert not allowed


def test_audio_upload_limit_and_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(museum, 'DB_PATH', str(tmp_path/'museum.sqlite3'))
    monkeypatch.setattr(museum, 'MEDIA_DIR', tmp_path/'media')
    c=museum._db()
    c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)', ('music-room',123,'Musiqa',time.time()))
    c.commit(); c.close()
    # Minimal MPEG frame header is sufficient for the server-side MP3 signature check.
    mp3=b'\xff\xfb\x90\x64'+b'\x00'*128
    payload={'init_data':signed_init(),'museum':'music-room','title':'test.mp3','audio':'data:audio/mpeg;base64,'+base64.b64encode(mp3).decode()}
    h=FakeHandler('/api/museum/audio',payload); museum.handle_post(h,'test-token')
    assert h.status==200
    c=museum._db(); assert c.execute('SELECT COUNT(*) FROM museum_audio').fetchone()[0]==1; c.close()
    h=FakeHandler('/api/museum/audio',dict(payload,init_data=signed_init(456))); museum.handle_post(h,'test-token')
    assert h.status==403


def test_music_control_requires_museum_access(tmp_path, monkeypatch):
    monkeypatch.setattr(museum, 'DB_PATH', str(tmp_path/'museum.sqlite3'))
    c=museum._db(); c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)', ('music-room',123,'Musiqa',time.time()))
    c.execute("INSERT INTO invites(token,museum_id,created,expires) VALUES(?,?,?,?)", ('guest-token','music-room',time.time(),time.time()+3600))
    c.execute("INSERT INTO museum_audio(id,museum_id,owner_id,title,storage_key,mime,created,sort_order) VALUES(?,?,?,?,?,?,?,?)", ('track','music-room',123,'Track','/tmp/no-file.mp3','audio/mpeg',time.time(),0)); c.commit(); c.close()
    payload={'init_data':signed_init(456),'museum':'music-room','invite':'guest-token','index':0,'playing':True}
    h=FakeHandler('/api/museum/music_control',payload); museum.handle_post(h,'test-token')
    assert h.status==200
    c=museum._db(); assert c.execute('SELECT music_playing FROM museums WHERE id=?',('music-room',)).fetchone()[0]==1; c.close()
