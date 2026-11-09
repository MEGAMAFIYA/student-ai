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
    assert h.status==200 and len(result['invite'])==36

def test_photo_requires_owner_and_valid_image(tmp_path, monkeypatch):
    monkeypatch.setattr(museum,'DB_PATH',str(tmp_path/'museum.sqlite3'))
    monkeypatch.setattr(museum,'MEDIA_DIR',tmp_path/'photos')
    c=museum._db(); c.execute('INSERT INTO museums(id,owner_id,title,created) VALUES(?,?,?,?)',('abc',123,'test',time.time())); c.commit(); c.close()
    png=b'\x89PNG\r\n\x1a\n'+b'0'*16
    payload={'init_data':signed_init(),'museum':'abc','title':'test.png','image':'data:image/png;base64,'+base64.b64encode(png).decode()}
    h=FakeHandler('/api/museum/photo',payload); museum.handle_post(h,'test-token')
    assert h.status==200 and json.loads(h.wfile.getvalue())['ok']
    h=FakeHandler('/api/museum/photo',dict(payload,init_data=signed_init(456))); museum.handle_post(h,'test-token')
    assert h.status==403
