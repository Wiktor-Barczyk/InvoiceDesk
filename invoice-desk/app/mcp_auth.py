"""Private first-party OAuth authorization code + PKCE, opaque rotating tokens."""
import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from .db import audit
from .domain import Problem
from .mcp_jobs import digest

SCOPE='invoices:prepare'
CALLBACK='https://chatgpt.com/connector_platform_oauth_redirect'


async def body(request, maximum=1024*1024):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>maximum:raise Problem('Żądanie jest zbyt duże.',413)
    return bytes(data)


class OAuth:
    def __init__(self,db,env):
        self.db=db
        self.base=env.get('MCP_PUBLIC_URL','').rstrip('/')
        u=urlsplit(self.base)
        if u.scheme!='https' or not u.netloc or u.username or u.password or u.path or u.query or u.fragment:
            raise RuntimeError('MCP_PUBLIC_URL musi być adresem HTTPS portalu bez ścieżki, np. https://invoices.example.org')
        self.resource=self.base+'/mcp'
        self.client=env.get('MCP_CLIENT_ID','')
        self.secret=env.get('MCP_CLIENT_SECRET','')
        self.signing=env['APP_SESSION_SECRET']
        if len(self.client)<16 or len(self.secret)<32:raise RuntimeError('Uruchom configure_mcp.py: brak danych klienta OAuth.')
        try:self.allowed={int(x) for x in env.get('MCP_ALLOWED_STAFF_IDS','').split(',') if x}
        except ValueError:raise RuntimeError('Nieprawidłowe MCP_ALLOWED_STAFF_IDS.') from None
        if not self.allowed:raise RuntimeError('Wskaż pracownika przez configure_mcp.py.')
        self.callbacks={CALLBACK}
        for value in env.get('MCP_REDIRECT_URIS','').split(','):
            if not value:continue
            if not re.fullmatch(r'https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]+',value):
                raise RuntimeError('Nieprawidłowy dodatkowy adres powrotu ChatGPT.')
            self.callbacks.add(value)

    def permitted(self,staff):
        return staff and staff['id'] in self.allowed and staff['role'] in ('admin','chief_accountant','accountant','reviewer')

    def challenge(self):
        return JSONResponse({'error':'invalid_token'},401,headers={'WWW-Authenticate':f'Bearer resource_metadata="{self.base}/.well-known/oauth-protected-resource", scope="{SCOPE}"'})

    def bearer(self,request):
        value=request.headers.get('authorization','')
        if not value.startswith('Bearer ') or not 20<=len(value)<=512:return None
        with self.db.connect() as c:
            row=c.execute("""SELECT g.*,u.role,u.id AS user_id FROM documents.mcp_grants g
                JOIN core.staff_users u ON u.id=g.staff_id WHERE g.access_hash=%s AND NOT g.revoked
                AND g.access_expires>now() AND u.is_active""",(digest(value[7:]),)).fetchone()
            if not row or row['staff_id'] not in self.allowed or row['role'] not in ('admin','chief_accountant','accountant','reviewer') or row['client_id']!=self.client or row['resource']!=self.resource:return None
            return row

    def signed(self,payload):
        value=base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return value+'.'+hmac.new(self.signing.encode(),value.encode(),hashlib.sha256).hexdigest()

    def verified(self,value):
        try:
            raw,sig=value.rsplit('.',1)
            if not hmac.compare_digest(self.signed(json.loads(base64.urlsafe_b64decode(raw))),value):return None
            p=json.loads(base64.urlsafe_b64decode(raw))
            return p if p['exp']>time.time() else None
        except (ValueError,TypeError,KeyError):return None

    def authorize_args(self,q):
        if q.get('client_id')!=self.client or q.get('redirect_uri') not in self.callbacks:raise Problem('Nieprawidłowy klient lub adres powrotu OAuth.',400)
        if q.get('response_type')!='code' or q.get('code_challenge_method')!='S256':raise Problem('Wymagany authorization_code z PKCE S256.',400)
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',q.get('code_challenge','')):raise Problem('Nieprawidłowe PKCE.',400)
        if q.get('resource')!=self.resource or q.get('scope')!=SCOPE:raise Problem('Nieprawidłowy zasób lub zakres dostępu.',400)
        if not 1<=len(q.get('state',''))<=2048:raise Problem('Brak lub nieprawidłowy OAuth state.',400)
        return {k:q[k] for k in ('client_id','redirect_uri','code_challenge','resource','scope','state','response_type','code_challenge_method')}

    def tokens(self,c,staff_id,grant_id=None):
        access,refresh=secrets.token_urlsafe(48),secrets.token_urlsafe(48)
        if grant_id:
            c.execute("""UPDATE documents.mcp_grants SET access_hash=%s,refresh_hash=%s,
                access_expires=now()+interval '1 hour' WHERE id=%s""",(digest(access),digest(refresh),grant_id))
        else:
            c.execute("""INSERT INTO documents.mcp_grants (staff_id,client_id,resource,access_hash,refresh_hash,access_expires,refresh_expires)
                VALUES (%s,%s,%s,%s,%s,now()+interval '1 hour',now()+interval '30 days')""",
                (staff_id,self.client,self.resource,digest(access),digest(refresh)))
        return {'access_token':access,'refresh_token':refresh,'expires_in':3600,'token_type':'Bearer','scope':SCOPE}

    def client_auth(self,request,form):
        client,secret=form.get('client_id',''),form.get('client_secret','')
        auth=request.headers.get('authorization','')
        if auth:
            try:
                if not auth.startswith('Basic '):return False
                client,secret=base64.b64decode(auth[6:],validate=True).decode().split(':',1)
            except (ValueError,UnicodeError):return False
        return hmac.compare_digest(client,self.client) and hmac.compare_digest(secret,self.secret)

    def exchange(self,form):
        if form.get('resource')!=self.resource:return None
        with self.db.connect() as c:
            c.execute('DELETE FROM documents.mcp_codes WHERE expires_at<=now()')
            kind=form.get('grant_type')
            if kind=='authorization_code':
                code=c.execute('SELECT * FROM documents.mcp_codes WHERE code_hash=%s AND expires_at>now() FOR UPDATE',(digest(form.get('code','')),)).fetchone()
                verifier=form.get('code_verifier','')
                challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                if not code or code['client_id']!=self.client or code['resource']!=self.resource or code['redirect_uri']!=form.get('redirect_uri') or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier) or not hmac.compare_digest(challenge,code['challenge']):return None
                staff=c.execute('SELECT id,role FROM core.staff_users WHERE id=%s AND is_active',(code['staff_id'],)).fetchone()
                if not self.permitted(staff):return None
                c.execute('DELETE FROM documents.mcp_codes WHERE code_hash=%s',(code['code_hash'],))
                return self.tokens(c,staff['id'])
            if kind=='refresh_token':
                grant=c.execute("SELECT * FROM documents.mcp_grants WHERE refresh_hash=%s AND NOT revoked AND refresh_expires>now() FOR UPDATE",(digest(form.get('refresh_token','')),)).fetchone()
                if not grant or grant['client_id']!=self.client or grant['resource']!=self.resource:return None
                staff=c.execute('SELECT id,role FROM core.staff_users WHERE id=%s AND is_active',(grant['staff_id'],)).fetchone()
                if not self.permitted(staff):return None
                if form.get('scope',SCOPE)!=SCOPE:return None
                return self.tokens(c,staff['id'],grant['id'])
            return None

    def install(self,app,writer):
        @app.get('/.well-known/oauth-protected-resource')
        @app.get('/.well-known/oauth-protected-resource/mcp')
        def resource():
            return {'resource':self.resource,'authorization_servers':[self.base],'scopes_supported':[SCOPE],
                    'bearer_methods_supported':['header'],'resource_name':'Invoice Desk — propozycje faktur'}

        @app.get('/.well-known/oauth-authorization-server')
        def metadata():
            return {'issuer':self.base,'authorization_endpoint':self.base+'/oauth/authorize','token_endpoint':self.base+'/oauth/token',
                'revocation_endpoint':self.base+'/oauth/revoke','response_types_supported':['code'],
                'grant_types_supported':['authorization_code','refresh_token'],
                'token_endpoint_auth_methods_supported':['client_secret_post','client_secret_basic'],
                'code_challenge_methods_supported':['S256'],'scopes_supported':[SCOPE],
                'authorization_response_iss_parameter_supported':True}

        @app.get('/oauth/authorize')
        def authorize(request:Request):
            q=self.authorize_args(dict(request.query_params));nonce=secrets.token_urlsafe(32)
            value=self.signed({**q,'nonce':digest(nonce),'exp':int(time.time())+600})
            markup=f'''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Połącz Invoice Desk z ChatGPT</title><link rel="stylesheet" href="/static/mcp-login.css?v=1.3">
<script defer src="/static/mcp-login.js?v=1.3"></script></head><body><main><h1>Połącz portal z ChatGPT</h1>
<p>ChatGPT uzyska dostęp do dokumentów wszystkich klientów widocznych na tym koncie oraz zapisze propozycje danych do sprawdzenia. Nie uzyska możliwości zatwierdzania faktur ani usuwania dokumentów.</p>
<p>Połączenie może być używane przez zadania okresowe. Uprawnienie wygasa po 30 dniach; można je wcześniej odwołać na serwerze.</p>
<form id="login"><label>E-mail pracownika<input name="email" type="email" autocomplete="username" required></label><label>Hasło<input name="password" type="password" autocomplete="current-password" required></label><button>Zaloguj się</button></form>
<p id="account"></p><button id="consent" hidden>Połącz i zezwól na zapis propozycji</button>
<p id="message" role="status"></p><input id="authorization" type="hidden" value="{html.escape(value,quote=True)}"></main></body></html>'''
            response=HTMLResponse(markup)
            response.set_cookie('mcp_oauth_nonce',nonce,max_age=600,httponly=True,secure=True,samesite='lax',path='/oauth')
            return response

        @app.post('/oauth/consent')
        async def consent(request:Request,staff=Depends(writer)):
            if not self.permitted(staff):raise Problem('Administrator nie udostępnił MCP temu pracownikowi.',403)
            try:p=json.loads(await body(request,16384));value=p['authorization']
            except (ValueError,KeyError,TypeError):raise Problem('Nieprawidłowa zgoda OAuth.',400)
            q=self.verified(value) if isinstance(value,str) else None
            if not q or not secrets.compare_digest(q['nonce'],digest(request.cookies.get('mcp_oauth_nonce',''))):raise Problem('Zgoda wygasła. Ponownie połącz portal.',400)
            self.authorize_args(q)
            code=secrets.token_urlsafe(48)
            def save():
                with self.db.connect() as c:
                    c.execute('DELETE FROM documents.mcp_codes WHERE expires_at<=now()')
                    c.execute("""INSERT INTO documents.mcp_codes (code_hash,staff_id,client_id,resource,redirect_uri,challenge,expires_at)
                        VALUES (%s,%s,%s,%s,%s,%s,now()+interval '2 minutes')""",
                        (digest(code),staff['id'],self.client,self.resource,q['redirect_uri'],q['code_challenge']))
                    audit(c,'core.staff_users',staff['id'],staff['id'],{'mcp_action':'oauth_consent','scope':SCOPE})
            await run_in_threadpool(save)
            response=JSONResponse({'redirect':q['redirect_uri']+'?'+urlencode({'code':code,'state':q['state'],'iss':self.base})})
            response.delete_cookie('mcp_oauth_nonce',path='/oauth')
            return response

        async def form_data(request):
            from urllib.parse import parse_qs
            data=await body(request,16384)
            try:
                values=parse_qs(data.decode(),max_num_fields=20,strict_parsing=True)
                if any(len(v)!=1 for v in values.values()):raise ValueError()
                return {k:v[0] for k,v in values.items()}
            except (UnicodeError,ValueError):raise Problem('Nieprawidłowe żądanie OAuth.',400)

        @app.post('/oauth/token')
        async def token(request:Request):
            form=await form_data(request)
            if not self.client_auth(request,form):return JSONResponse({'error':'invalid_client'},401)
            result=await run_in_threadpool(self.exchange,form)
            return JSONResponse(result or {'error':'invalid_grant'},status_code=200 if result else 400)

        @app.post('/oauth/revoke')
        async def revoke(request:Request):
            form=await form_data(request)
            if not self.client_auth(request,form):return JSONResponse({'error':'invalid_client'},401)
            def save():
                with self.db.connect() as c:
                    hashed=digest(form.get('token',''))
                    c.execute('UPDATE documents.mcp_grants SET revoked=true WHERE client_id=%s AND (access_hash=%s OR refresh_hash=%s)',(self.client,hashed,hashed))
            await run_in_threadpool(save)
            return JSONResponse({})
