import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.mcp_auth import CALLBACK,SCOPE
from app.mcp_jobs import Jobs,manage
from app import processing
from app.domain import Problem
from test_flow import rig,upload
from test_processing import proposal

ROOT=Path(__file__).resolve().parents[1]
BASE='https://invoice.test'


@pytest.fixture
def mcp(rig,monkeypatch):
    import os
    with rig['db'].connect() as c:
        for _ in range(2):
            for path in sorted((ROOT/'app/migrations').glob('*.sql')):c.execute(path.read_text())
    env={'DATABASE_URL':os.environ['TEST_DATABASE_URL'],'APP_SESSION_SECRET':'test-only-secret-'*4,
        'DOCUMENT_STORAGE':str(rig['storage']),'BACKGROUND_PROCESSING':'1','AI_ENABLED':'0','AI_PROVIDER':'mcp',
        'MCP_PUBLIC_URL':BASE,'APP_ORIGIN':BASE,'COOKIE_SECURE':'1','MCP_CLIENT_ID':'invoice-desk-test-client',
        'MCP_CLIENT_SECRET':'test-client-secret-'*4,'MCP_ALLOWED_STAFF_IDS':str(rig['staff']['id'])}
    with TestClient(create_app(env),base_url=BASE) as http:
        assert http.post('/api/login',json={'email':rig['staff']['email'],'password':'test-password-123'}).status_code==200
        http.headers['X-CSRF-Token']=http.get('/api/me').json()['csrf']
        yield {**rig,'http':http,'env':env}
    with rig['db'].connect() as c:
        c.execute("UPDATE documents.processing_jobs SET status='cancelled' WHERE mode='mcp' AND accounting_document_id IN (SELECT id FROM documents.accounting_documents WHERE client_id=%s)",(rig['client']['id'],))


def connect(rig):
    http=rig['http'];env=rig['env'];verifier='test-pkce-verifier-'*4
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    q={'client_id':env['MCP_CLIENT_ID'],'redirect_uri':CALLBACK,'resource':BASE+'/mcp','scope':SCOPE,
       'response_type':'code','code_challenge_method':'S256','code_challenge':challenge,'state':'state-test'}
    r=http.get('/oauth/authorize',params=q);assert r.status_code==200,r.text
    authorization=re.search(r'id="authorization" type="hidden" value="([^"]+)"',r.text)[1]
    r=http.post('/oauth/consent',json={'authorization':authorization});assert r.status_code==200,r.text
    params=parse_qs(urlsplit(r.json()['redirect']).query)
    assert params['state']==['state-test'] and params['iss']==[BASE]
    form={'grant_type':'authorization_code','code':params['code'][0],'client_id':env['MCP_CLIENT_ID'],
          'client_secret':env['MCP_CLIENT_SECRET'],'redirect_uri':CALLBACK,'resource':BASE+'/mcp','code_verifier':verifier}
    r=http.post('/oauth/token',headers={'Authorization':''},data={**form,'code_verifier':'wrong'*12});assert r.status_code==400
    r=http.post('/oauth/token',headers={'Authorization':''},data=form);assert r.status_code==200,r.text
    assert http.post('/oauth/token',headers={'Authorization':''},data=form).status_code==400
    http.headers['Authorization']='Bearer '+r.json()['access_token']
    return r.json()


def rpc(rig,method,params={}):
    response=rig['http'].post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
    assert response.status_code==200,response.text
    return response.json()


def call(rig,name,**args):
    return rpc(rig,'tools/call',{'name':name,'arguments':args})['result']


def claim(rig):
    return call(rig,'next_invoice',client_id=rig['client']['id'])['structuredContent']


def pages(rig,task):
    args={k:task[k] for k in ('document_id','lease_token')}
    for f in task['files']:
        for page in range(1,f['pages']+1):
            r=call(rig,'get_invoice_page',**args,file_id=f['file_id'],page=page)
            assert not r['isError'] and r['content'][1]['mimeType']=='image/jpeg'
    return args


def test_oauth_protocol_and_proposal(mcp,monkeypatch):
    monkeypatch.setattr('app.ai.call_openai',lambda *_:pytest.fail('MCP must not call OpenAI API'))
    http=mcp['http']
    assert http.post('/mcp',json={}).status_code==401
    assert 'resource_metadata' in http.post('/mcp',json={}).headers['www-authenticate']
    connect(mcp)
    assert rpc(mcp,'initialize',{'protocolVersion':'2025-06-18'})['result']['protocolVersion']=='2025-06-18'
    assert len(rpc(mcp,'tools/list')['result']['tools'])==5
    assert http.post('/mcp',json={'jsonrpc':'2.0','method':'notifications/initialized'}).status_code==202
    doc=upload(mcp)['id'];task=claim(mcp);assert task['document_id']==doc
    assert claim(mcp)['document'] is None
    args={k:task[k] for k in ('document_id','lease_token')}
    assert call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp))['isError']
    pages(mcp,task)
    result=call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp))
    assert not result['isError'],result
    assert result['structuredContent']['status']=='ready'
    assert call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp))['structuredContent']['already_saved']
    different=proposal(mcp);different['fields']['gross_total']='888.00'
    assert call(mcp,'save_invoice_proposal',**args,proposal=different)['isError']
    state=http.get(f'/api/documents/{doc}').json()
    assert state['invoice'] is None and state['ocr']['ocr_engine']=='chatgpt-mcp'
    assert state['ocr']['extracted_fields']['gross_total']=='121.00'
    assert claim(mcp)['document'] is None
    with mcp['db'].connect() as c:
        assert c.execute('SELECT count(*) AS n FROM documents.document_ocr_runs WHERE accounting_document_id=%s',(doc,)).fetchone()['n']==1
    report=call(mcp,'invoice_queue_report',client_id=mcp['client']['id'])['structuredContent']
    assert report['counts']['ready']==1


def test_refresh_revocation_and_scope(mcp):
    tokens=connect(mcp);http=mcp['http'];env=mcp['env']
    form={'grant_type':'refresh_token','refresh_token':tokens['refresh_token'],
        'client_id':env['MCP_CLIENT_ID'],'client_secret':env['MCP_CLIENT_SECRET'],'resource':BASE+'/mcp'}
    assert http.post('/oauth/token',headers={'Authorization':''},data={**form,'resource':'https://attacker.test/mcp'}).status_code==400
    r=http.post('/oauth/token',headers={'Authorization':''},data=form);assert r.status_code==200
    assert http.post('/oauth/token',headers={'Authorization':''},data=form).status_code==400
    assert http.post('/mcp',json={}).status_code==401 # old access token invalidated
    http.headers['Authorization']='Bearer '+r.json()['access_token']
    assert rpc(mcp,'ping')['result']=={}
    revoked=http.post('/oauth/revoke',headers={'Authorization':''},data={'client_id':env['MCP_CLIENT_ID'],'client_secret':env['MCP_CLIENT_SECRET'],'token':r.json()['refresh_token']})
    assert revoked.status_code==200
    assert http.post('/mcp',json={}).status_code==401


def test_auth_origin_role_and_untrusted_input(mcp):
    http=mcp['http'];connect(mcp)
    assert http.post('/mcp',headers={'Origin':'https://attacker.test'},json={}).status_code==403
    assert http.get('/oauth/authorize',params={'client_id':mcp['env']['MCP_CLIENT_ID'],'redirect_uri':'https://attacker.test'}).status_code==400
    assert 'error' in rpc(mcp,'tools/call',{'name':'approve_invoice'})
    assert call(mcp,'next_invoice',client_id=True)['isError']
    assert call(mcp,'next_invoice',client_id=mcp['client']['id'],sql='SELECT password')['isError']
    assert http.post('/mcp',content=b'x'*(1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413
    with mcp['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(mcp['staff']['id'],))
    assert http.post('/mcp',json={}).status_code==401


def test_split_and_stale_lease(mcp):
    connect(mcp);doc=upload(mcp)['id'];task=claim(mcp);args=pages(mcp,task)
    r=call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp,True))
    assert r['structuredContent']['needs_split'] and r['structuredContent']['status']=='review'
    next_doc=upload(mcp,True)['id'];old=claim(mcp)
    with mcp['db'].connect() as c:c.execute("UPDATE documents.mcp_claims SET expires_at=now()-interval '1 second' WHERE accounting_document_id=%s",(next_doc,))
    fresh=claim(mcp);assert fresh['document_id']==next_doc and old['lease_token']!=fresh['lease_token']
    assert call(mcp,'get_invoice_page',document_id=next_doc,lease_token=old['lease_token'],file_id=old['files'][0]['file_id'],page=1)['isError']
    args=pages(mcp,fresh)
    with mcp['db'].connect() as c:c.execute('UPDATE documents.accounting_documents SET updated_at=clock_timestamp() WHERE id=%s',(next_doc,))
    assert call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp))['isError']
    assert mcp['http'].get(f'/api/documents/{next_doc}').json()['ocr'] is None


def test_concurrent_accountant_approval(mcp):
    connect(mcp);doc=upload(mcp)['id'];task=claim(mcp);args=pages(mcp,task)
    initial=mcp['http'].get(f'/api/documents/{doc}').json()
    p={**proposal(mcp)['fields'],'client_id':mcp['client']['id'],'reviewed':True,'version':initial['document']['updated_at']}
    assert mcp['http'].post(f'/api/documents/{doc}/approve',json=p).status_code==200
    assert call(mcp,'save_invoice_proposal',**args,proposal=proposal(mcp))['isError']
    assert mcp['http'].get(f'/api/documents/{doc}').json()['invoice']['status']=='approved'


def test_missing_file_release_and_migration(mcp):
    connect(mcp);doc=upload(mcp)['id']
    with mcp['db'].connect() as c:c.execute("UPDATE documents.processing_jobs SET mode='ai',status='failed',attempts=3 WHERE accounting_document_id=%s",(doc,))
    manage(mcp['db'],'enable-mcp')
    task=claim(mcp);assert task['document_id']==doc
    args={k:task[k] for k in ('document_id','lease_token')}
    r=call(mcp,'release_invoice',**args,reason='Limit ChatGPT',retry_later=True)
    assert r['structuredContent']['status']=='queued'
    assert claim(mcp)['document'] is None
    with mcp['db'].connect() as c:c.execute("UPDATE documents.processing_jobs SET available_at=now() WHERE accounting_document_id=%s",(doc,))
    for p in mcp['storage'].rglob('*.pdf'):p.unlink()
    assert claim(mcp)['status']=='failed'
    with mcp['db'].connect() as c:assert processing.job(c,doc)['status']=='failed'


def test_official_sdk_transport(mcp):
    """Optional interoperability check; install requirements-test-mcp.txt."""
    pytest.importorskip('mcp')
    import asyncio
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    tokens=connect(mcp)
    doc=upload(mcp)['id']

    def factory(headers=None,timeout=None,auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp['http'].app),
            headers=headers,timeout=timeout,auth=auth,follow_redirects=True)

    async def flow():
        async with streamablehttp_client(BASE+'/mcp',headers={'Authorization':'Bearer '+tokens['access_token']},
                httpx_client_factory=factory) as (read,write,_):
            async with ClientSession(read,write) as session:
                info=await session.initialize()
                assert info.serverInfo.version=='1.4.1'
                assert len((await session.list_tools()).tools)==5
                r=await session.call_tool('next_invoice',{'client_id':mcp['client']['id']})
                assert not r.isError and r.structuredContent['document_id']==doc
                task=r.structuredContent
                args={k:task[k] for k in ('document_id','lease_token')}
                for f in task['files']:
                    for page in range(1,f['pages']+1):
                        r=await session.call_tool('get_invoice_page',{**args,'file_id':f['file_id'],'page':page})
                        assert not r.isError and r.content[1].type=='image'
                r=await session.call_tool('save_invoice_proposal',{**args,'proposal':proposal(mcp)})
                assert not r.isError and r.structuredContent['status']=='ready'
                report=await session.call_tool('invoice_queue_report',{'client_id':mcp['client']['id']})
                assert report.structuredContent['counts']['ready']==1
    asyncio.run(flow())
