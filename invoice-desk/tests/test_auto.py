import io
import os

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auto_split
from app.main import create_app
from test_flow import rig as base_rig, payload, upload
from test_splitting import receipt_image

DETECT=auto_split.detect
REGIONS=[{'page':1,'box':[0,0,.5,1],'rotation':0},{'page':1,'box':[.5,0,1,1],'rotation':0}]


@pytest.fixture
def rig(base_rig,monkeypatch):
    monkeypatch.setattr(auto_split,'detect',lambda *a:{'status':'manual','regions':[],'message':'Nie wykryto podziału.'})
    return base_rig


def confident(monkeypatch):
    monkeypatch.setattr(auto_split,'detect',lambda *a:{'status':'confident','regions':REGIONS.copy()})


def send(rig):
    r=rig['http'].post('/api/documents/upload',data={'client_id':rig['client']['id']},
        files={'file':('dwa-paragony.png',receipt_image(),'image/png')})
    assert r.status_code==200,r.text
    return r.json()


def state(rig,doc):return rig['http'].get(f'/api/documents/{doc}').json()


def test_scanned_layouts(tmp_path):
    raw=receipt_image();image=Image.open(io.BytesIO(raw))
    for name,im,n in [('horizontal',image,2),('vertical',image.transpose(Image.Transpose.ROTATE_90),2)]:
        # Vertical arrangement has upright text, not rotated receipts.
        if name=='vertical':
            im=Image.new('RGB',(700,1700),'white')
            im.paste(image.crop((0,0,700,850)),(0,0));im.paste(image.crop((700,0,1400,850)),(0,850))
        p=tmp_path/(name+'.png');im.save(p)
        result=DETECT(p,'eng');assert result['status']=='confident',result
        assert len(result['regions'])==n
        assert abs(sum((r['box'][2]-r['box'][0])*(r['box'][3]-r['box'][1]) for r in result['regions'])-1)<1e-6
    grid=Image.new('RGB',(1400,1700),'white');grid.paste(image,(0,0));grid.paste(image,(0,850));p=tmp_path/'grid.png';grid.save(p)
    assert len(DETECT(p,'eng')['regions'])==4
    p=tmp_path/'single.png';image.crop((0,0,700,850)).save(p)
    assert DETECT(p,'eng')['status']=='manual'


def test_multipage_and_missing_ocr_are_manual(tmp_path,monkeypatch):
    p=tmp_path/'two.pdf'
    with fitz.open() as pdf:
        pdf.new_page();pdf.new_page();pdf.save(p)
    assert DETECT(p,'eng')['status']=='manual'
    p=tmp_path/'two.png';p.write_bytes(receipt_image())
    def broken(*a,**kw):raise FileNotFoundError('tesseract')
    monkeypatch.setattr(auto_split.subprocess,'run',broken)
    assert DETECT(p,'eng')['status']=='manual'


def test_upload_and_reupload_auto_split(rig,monkeypatch):
    monkeypatch.setattr(auto_split,'detect',DETECT);result=send(rig);parent=result['id'];children=result['split_children']
    assert len(children)==2
    assert state(rig,parent)['document']['status']=='archived'
    before=rig['http'].get(f'/api/documents/{parent}/file').content
    repeat=send(rig)
    assert repeat['duplicate'] and repeat['split_children']==children
    assert before==receipt_image()
    assert [d['id'] for d in rig['http'].get('/api/documents',params={'client_id':rig['client']['id']}).json()]==[c['id'] for c in reversed(children)]
    for ch in children:
        s=state(rig,ch['id']);assert s['split']['is_child'] and s['document']['status']=='to_book'
    # Booking one child must never approve a sibling.
    first=children[0]['id'];saved=rig['http'].post(f'/api/documents/{first}/approve',json=payload(rig,first))
    assert saved.status_code==200,saved.text
    assert state(rig,children[1]['id'])['invoice'] is None


def test_existing_auto_endpoint_idempotent_and_child_guard(rig,monkeypatch):
    parent=send(rig)['id'];s=state(rig,parent);confident(monkeypatch)
    data={'version':s['document']['updated_at']}
    first=rig['http'].post(f'/api/documents/{parent}/auto-split',json=data)
    assert first.status_code==200,first.text
    again=rig['http'].post(f'/api/documents/{parent}/auto-split',json=data)
    assert again.json()['already_split'] and first.json()['children']==again.json()['children']
    child=first.json()['children'][0]['id'];s=state(rig,child)
    assert rig['http'].post(f'/api/documents/{child}/auto-split',json={'version':s['document']['updated_at']}).json()['status']=='manual'
    with rig['db'].connect() as c:
        batch=c.execute('SELECT import_details FROM documents.document_imports WHERE client_id=%s',(rig['client']['id'],)).fetchone()
        assert batch['import_details']['method']=='automatic'


def test_review_does_not_write_or_archive(rig,monkeypatch):
    monkeypatch.setattr(auto_split,'detect',lambda *a:{'status':'review','regions':REGIONS.copy()})
    result=send(rig);assert result['auto_split']['status']=='review' and 'split_children' not in result
    s=state(rig,result['id']);assert s['split'] is None and s['document']['status']=='to_book'
    r=rig['http'].post(f"/api/documents/{result['id']}/auto-split",json={'version':s['document']['updated_at']})
    assert r.json()['status']=='review'
    assert state(rig,result['id'])['document']['updated_at']==s['document']['updated_at']


def test_permissions_stale_and_saved_guard(rig,monkeypatch):
    parent=send(rig)['id'];s=state(rig,parent);confident(monkeypatch)
    data={'version':s['document']['updated_at']};url=f'/api/documents/{parent}/auto-split'
    assert rig['http'].post(url,json={'version':'stale'}).status_code==409
    assert rig['http'].post(url,json={'version':False}).status_code==422
    token=rig['http'].headers.pop('X-CSRF-Token')
    assert rig['http'].post(url,json=data).status_code==403
    rig['http'].headers['X-CSRF-Token']=token
    with rig['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(rig['staff']['id'],))
    assert rig['http'].post(url,json=data).status_code==403
    with rig['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='accountant' WHERE id=%s",(rig['staff']['id'],))
    monkeypatch.setattr(auto_split,'detect',lambda *a:{'status':'manual','regions':[]})
    saved_doc=upload(rig)['id']
    r=rig['http'].post(f'/api/documents/{saved_doc}/approve',json=payload(rig,saved_doc));assert r.status_code==200,r.text
    saved_state=state(rig,saved_doc)
    assert rig['http'].post(f'/api/documents/{saved_doc}/auto-split',json={'version':saved_state['document']['updated_at']}).status_code==409


def test_failure_upload_keeps_original_and_rolls_back_children(rig,monkeypatch):
    confident(monkeypatch)
    with rig['db'].connect() as c:
        c.execute("""CREATE FUNCTION documents.test_auto_failure() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.source_key='2' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END$$""")
        c.execute('CREATE TRIGGER test_auto_failure BEFORE INSERT ON documents.document_import_items FOR EACH ROW EXECUTE FUNCTION documents.test_auto_failure()')
    try:
        result=send(rig);assert result['auto_split']['status']=='manual'
        assert state(rig,result['id'])['document']['status']=='to_book'
        with rig['db'].connect() as c:
            assert c.execute('SELECT count(*) AS n FROM documents.accounting_documents WHERE client_id=%s',(rig['client']['id'],)).fetchone()['n']==1
            assert c.execute('SELECT count(*) AS n FROM documents.document_imports WHERE client_id=%s',(rig['client']['id'],)).fetchone()['n']==0
    finally:
        with rig['db'].connect() as c:
            c.execute('DROP TRIGGER test_auto_failure ON documents.document_import_items');c.execute('DROP FUNCTION documents.test_auto_failure()')


def test_undone_split_requires_explicit_retry(rig,monkeypatch):
    confident(monkeypatch);result=send(rig);parent=result['id'];s=state(rig,parent)
    undo=rig['http'].post(f'/api/documents/{parent}/unsplit',json={'version':s['document']['updated_at'],'batch_id':s['split']['batch_id']})
    assert undo.status_code==200,undo.text
    s=state(rig,parent);url=f'/api/documents/{parent}/auto-split';data={'version':s['document']['updated_at']}
    assert rig['http'].post(url,json=data).json()['status']=='manual'
    assert rig['http'].post(url,json={**data,'force':True}).json()['status']=='split'


def test_background_mcp_children_queued_without_api_key(rig,monkeypatch):
    confident(monkeypatch)
    app=create_app({'DATABASE_URL':os.environ['TEST_DATABASE_URL'],'APP_SESSION_SECRET':'test-background-'*4,
        'DOCUMENT_STORAGE':str(rig['storage']),'OCR_LANGUAGES':'eng','BACKGROUND_PROCESSING':'1','AI_PROVIDER':'mcp',
        'MCP_PUBLIC_URL':'https://invoices.example.test','MCP_CLIENT_ID':'test-client-id-123','MCP_CLIENT_SECRET':'test-secret-'*4,
        'MCP_ALLOWED_STAFF_IDS':str(rig['staff']['id'])})
    with TestClient(app) as http:
        http.post('/api/login',json={'email':rig['staff']['email'],'password':'test-password-123'})
        http.headers['X-CSRF-Token']=http.get('/api/me').json()['csrf']
        response=http.post('/api/documents/upload',data={'client_id':rig['client']['id']},files={'file':('two.png',receipt_image(),'image/png')})
        assert response.status_code==200,response.text
        result=response.json();assert len(result['split_children'])==2
        with rig['db'].connect() as c:
            jobs=c.execute('SELECT j.mode,j.status,j.accounting_document_id FROM documents.processing_jobs j JOIN documents.accounting_documents d ON d.id=j.accounting_document_id WHERE d.client_id=%s',(rig['client']['id'],)).fetchall()
        assert len(jobs)==2 and all(j['mode']=='mcp' and j['status']=='queued' for j in jobs)

