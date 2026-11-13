import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import processing
from app.ai import AIError, SCHEMA, call_openai, make_payload, sanitize
from app.db import document, references, insert
from app.main import create_app
from app.storage import FileStore
from app.worker import process_one
from test_flow import rig, upload

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bg(rig):
    with rig['db'].connect() as c:
        c.execute((ROOT/'app/migrations/001_processing.sql').read_text())
        c.execute((ROOT/'app/migrations/001_processing.sql').read_text())  # repeatable migration
    import os
    env = {'DATABASE_URL':os.environ['TEST_DATABASE_URL'],'APP_SESSION_SECRET':'test-only-secret-'*4,
           'DOCUMENT_STORAGE':str(rig['storage']),'OCR_LANGUAGES':'eng',
           'BACKGROUND_PROCESSING':'1','AI_ENABLED':'1','OPENAI_API_KEY':'test-key-never-sent'}
    with TestClient(create_app(env)) as http:
        assert http.post('/api/login',json={'email':rig['staff']['email'],'password':'test-password-123'}).status_code==200
        http.headers['X-CSRF-Token']=http.get('/api/me').json()['csrf']
        yield {**rig,'http':http,'env':env,'store':FileStore(rig['storage'])}
    with rig['db'].connect() as c:
        c.execute("""UPDATE documents.processing_jobs SET status='cancelled' WHERE accounting_document_id IN
            (SELECT id FROM documents.accounting_documents WHERE client_id=%s)""",(rig['client']['id'],))


def proposal(rig, multiple=False):
    f = {key:None for key in SCHEMA['properties']['fields']['properties']}
    f.update(invoice_type='purchase',invoice_kind='invoice',invoice_number='TEST-AI-1',invoice_date='2026-10-01',
        currency='EUR',seller_name_snapshot='Sprzedawca testowy',
        buyer_name_snapshot=rig['client']['company_name'],net_total='100.00',vat_total='21.00',gross_total='121.00',
        lines=[{'description':'Materiały','quantity':'1','unit_price':'100.00','net_amount':'100.00','vat_amount':'21.00',
                'booking_category_id':rig['category']['id'],'account_id':rig['account']['id'],'vat_code_id':rig['vat']['id']}])
    return {'fields':f,'multiple_documents':multiple,'warnings':[],'uncertain_fields':[]}


def get_refs(bg):
    with bg['db'].connect() as c:
        return references(c,bg['client']['id'])


def take(bg):
    with bg['db'].connect() as c:
        return processing.claim(c,'ai')


def fake_result(bg, multiple=False):
    return sanitize(proposal(bg,multiple),get_refs(bg),{'model':'test-model','response_id':'test-response','usage':{}})


def test_async_upload_and_worker_prefills_without_approval(bg,monkeypatch):
    monkeypatch.setattr('app.main.extract',lambda *_:pytest.fail('Upload must not run OCR'))
    doc=upload(bg)['id'];state=bg['http'].get(f'/api/documents/{doc}').json()
    assert state['ocr'] is None and state['preparation']['status']=='queued'
    task=take(bg);assert task['accounting_document_id']==doc
    monkeypatch.setattr('app.worker.extract_ai',lambda *_:fake_result(bg))
    process_one(bg['db'],bg['store'],bg['env'],task)
    ready=bg['http'].get(f'/api/documents/{doc}').json()
    assert ready['invoice'] is None and ready['ocr']['extracted_fields']['gross_total']=='121.00'
    assert ready['document']['updated_at']!=state['document']['updated_at']
    assert ready['preparation']['status']=='ready'
    replay_result=fake_result(bg)
    with bg['db'].connect() as c:
        assert processing.enqueue(c,'ai',doc_id=doc)==[]
        assert not processing.complete(c,task,document(c,doc)['document']['updated_at'],replay_result)
        assert c.execute('SELECT count(*) AS n FROM documents.document_ocr_runs WHERE accounting_document_id=%s',(doc,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) AS n FROM accounting.invoices WHERE client_id=%s',(bg['client']['id'],)).fetchone()['n']==0
    status=bg['http'].get('/api/processing',params={'client_id':bg['client']['id'],'doc_ids':str(doc)}).json()
    assert status['counts']['ready']==1 and status['jobs'][0]['status']=='ready'


def test_ai_does_not_overwrite_concurrent_approval(bg,monkeypatch):
    doc=upload(bg)['id'];task=take(bg)
    initial=bg['http'].get(f'/api/documents/{doc}').json()
    def during_read(*_):
        payload={**proposal(bg)['fields'],'client_id':bg['client']['id'],'reviewed':True,'version':initial['document']['updated_at']}
        answer=bg['http'].post(f'/api/documents/{doc}/approve',json=payload)
        assert answer.status_code==200,answer.text
        return fake_result(bg)
    monkeypatch.setattr('app.worker.extract_ai',during_read)
    process_one(bg['db'],bg['store'],bg['env'],task)
    state=bg['http'].get(f'/api/documents/{doc}').json()
    assert state['invoice']['status']=='approved' and state['ocr'] is None
    assert state['preparation']['status']=='cancelled'


def test_ai_discards_changed_document(bg,monkeypatch):
    doc=upload(bg)['id'];task=take(bg)
    def changed(*_):
        with bg['db'].connect() as c:
            c.execute("UPDATE documents.accounting_documents SET updated_at=clock_timestamp(),description='Zmienione' WHERE id=%s",(doc,))
        return fake_result(bg)
    monkeypatch.setattr('app.worker.extract_ai',changed)
    process_one(bg['db'],bg['store'],bg['env'],task)
    state=bg['http'].get(f'/api/documents/{doc}').json()
    assert state['ocr'] is None and state['preparation']['status']=='cancelled'


def test_retry_limit_backoff_and_restart_recovery(bg,monkeypatch):
    doc=upload(bg)['id']
    def throttled(*_):raise AIError('Limit testowy',True,60)
    monkeypatch.setattr('app.worker.extract_ai',throttled)
    for attempt in range(1,4):
        task=take(bg);assert task['attempts']==attempt
        process_one(bg['db'],bg['store'],bg['env'],task)
        with bg['db'].connect() as c:
            current=processing.job(c,doc)
            assert current['status']==('queued' if attempt<3 else 'failed')
            assert current['available_at']>current['updated_at']
            assert processing.claim(c,'ai') is None
            c.execute('UPDATE documents.processing_jobs SET available_at=now() WHERE accounting_document_id=%s',(doc,))
    assert bg['http'].post('/api/processing/retry',params={'client_id':bg['client']['id']}).json()['queued']==1
    assert bg['http'].post(f'/api/documents/{doc}/prepare').json()['status']=='queued'
    task=take(bg)
    with bg['db'].connect() as c:
        processing.recover(c)
        assert processing.job(c,doc)['status']=='queued'
        assert processing.job(c,doc)['attempts']==1


def test_multiple_documents_and_background_split(bg,monkeypatch):
    from test_splitting import receipt_image
    monkeypatch.setattr('app.auto_split.detect', lambda *args: {'status':'manual','regions':[]})
    raw=receipt_image()
    result=bg['http'].post('/api/documents/upload',data={'client_id':bg['client']['id']},files={'file':('two.png',raw,'image/png')})
    doc=result.json()['id'];task=take(bg)
    monkeypatch.setattr('app.worker.extract_ai',lambda *_:fake_result(bg,True))
    process_one(bg['db'],bg['store'],bg['env'],task)
    state=bg['http'].get(f'/api/documents/{doc}').json()
    assert state['ocr']['extracted_fields']=={} and state['preparation']['needs_split']
    monkeypatch.setattr('app.splitting.extract',lambda *_:pytest.fail('Split must not run OCR'))
    split=bg['http'].post(f'/api/documents/{doc}/split',json={'version':state['document']['updated_at'],'file_id':state['files'][0]['id'],
        'regions':[{'page':1,'box':[0,0,.5,1],'rotation':0},{'page':1,'box':[.5,0,1,1],'rotation':0}]})
    assert split.status_code==200,split.text
    for child in split.json()['children']:
        state=bg['http'].get(f"/api/documents/{child['id']}").json()
        assert state['ocr'] is None and state['preparation']['status']=='queued'
    parent=bg['http'].get(f'/api/documents/{doc}').json()
    assert parent['preparation']['status']=='cancelled'
    assert bg['http'].post(f'/api/documents/{doc}/unsplit',json={'version':parent['document']['updated_at'],'batch_id':parent['split']['batch_id']}).status_code==200
    assert take(bg) is None  # withdrawn children are cancelled before claiming


def test_prepare_csrf_and_roles(bg):
    doc=upload(bg)['id'];http=bg['http'];token=http.headers.pop('X-CSRF-Token')
    assert http.post(f'/api/documents/{doc}/prepare').status_code==403
    http.headers['X-CSRF-Token']=token
    with bg['db'].connect() as c:
        c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(bg['staff']['id'],))
    assert http.post(f'/api/documents/{doc}/prepare').status_code==403
    assert http.get(f'/api/documents/{doc}/preparation').status_code==200
    http.cookies.clear()
    assert http.get(f'/api/documents/{doc}/preparation').status_code==401


def test_pagination_and_server_search_beyond_first_page(bg):
    doc=upload(bg)['id']
    with bg['db'].connect() as c:
        for n in range(75):
            insert(c,'documents.accounting_documents',{'client_id':bg['client']['id'],'document_type':'receipt','source':'api',
                    'description':f'Pozycja_{n:03d}','status':'to_book'})
    path='/api/documents';params={'client_id':bg['client']['id'],'status':'pending'}
    first=bg['http'].get(path,params=params).json()
    second=bg['http'].get(path,params={**params,'offset':50}).json()
    assert len(first)==50 and len(second)==26 and first[0]['total_count']==76
    assert not {x['id'] for x in first}&{x['id'] for x in second}
    found=bg['http'].get(path,params={**params,'search':'Pozycja_000'}).json()
    assert len(found)==1 and found[0]['description']=='Pozycja_000'
    assert bg['http'].get(path,params={**params,'search':'%'}).json()==[]
    assert bg['http'].get(path,params={**params,'status':'bad'}).status_code==422


def test_preview_cache_preserves_auth_and_hash_integrity(bg,monkeypatch):
    import app.storage as storage_module
    calls=[];original=storage_module.image_bytes
    def counted(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(storage_module,'image_bytes',counted)
    doc=upload(bg)['id'];url=f'/api/documents/{doc}/preview/1'
    assert bg['http'].get(url).status_code==200
    assert bg['http'].get(url).status_code==200 and len(calls)==1
    with bg['db'].connect() as c:path,_=bg['store'].resolve(c,doc)
    raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    assert bg['http'].get(url).status_code==409
    bg['http'].cookies.clear();assert bg['http'].get(url).status_code==401


def test_ai_schema_unknown_ids_and_bad_totals(bg):
    p=proposal(bg);p['fields']['lines'][0]['account_id']=999999999
    p['fields']['gross_total']='999.00';p['fields']['seller_iban_snapshot']='x'*35
    result=sanitize(p,get_refs(bg),{})
    # An explicit category mapping supplies the canonical account after an unknown ID is rejected.
    assert result['fields']['lines'][0]['account_id']==bg['account']['id']
    assert result['fields']['seller_iban_snapshot'] is None
    assert any('Suma' in w for w in result['raw']['warnings'])
    p=proposal(bg);p['fields']['reviewed']=True
    with pytest.raises(AIError):sanitize(p,get_refs(bg),{})
    p=proposal(bg);p['fields']['lines'][0]['account_id']=True
    with pytest.raises(AIError):sanitize(p,get_refs(bg),{})


def test_responses_payload_and_transport_without_live_api(bg,monkeypatch):
    doc=upload(bg)['id']
    with bg['db'].connect() as c:path,_=bg['store'].resolve(c,doc)
    payload=make_payload([path],bg['store'],get_refs(bg),'gpt-4.1-mini')
    assert payload['store'] is False and payload['text']['format']['strict']
    assert any(x['type']=='input_image' and x['image_url'].startswith('data:image/jpeg;base64,') for x in payload['input'][0]['content'])
    assert 'password' not in json.dumps(payload) and 'test-key' not in json.dumps(payload)
    returned={'status':'completed','id':'test','model':'test','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(proposal(bg))}]}]}
    class Transport:
        def open(self,req,timeout):
            assert req.full_url=='https://api.openai.com/v1/responses' and timeout==120
            return self
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,_):return json.dumps(returned).encode()
    monkeypatch.setattr('app.ai.request.build_opener',lambda *_:Transport())
    result,meta=call_openai(payload,'test-key')
    assert result['fields']['gross_total']=='121.00' and meta['response_id']=='test'
    returned['status']='incomplete'
    with pytest.raises(AIError):call_openai(payload,'test-key')
    returned['status']='completed';returned['output'][0]['content']=[{'type':'refusal','refusal':'no'}]
    with pytest.raises(AIError):call_openai(payload,'test-key')
