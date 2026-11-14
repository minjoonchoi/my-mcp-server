#!/usr/bin/env python3
"""Real loopback HTTP/MCP integration checks; Python 3 standard library only."""
import base64
import zipfile
import concurrent.futures
import contextlib
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "build" / "integration"
PROTOCOL = "2025-06-18"
SECRET = "INTERNAL_SECRET_DO_NOT_LEAK"
API_PROFILE = os.environ.get("INTEGRATION_API_PROFILE", "fixture")

def claims(token):
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))

def profile(subject):
    return {"user": {"id": subject, "name": "Test", "description": "Test", "roleIds": []}, "roles": [], "policies": [], "resources": []}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(port, path, token=None, body=None, extra=None, method=None, chunked=False):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
        if not isinstance(body, (bytes, list)):
            body = json.dumps(body).encode()
    headers.update(extra or {})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method or ("POST" if body is not None else "GET"), path, body, headers, encode_chunked=chunked)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        conn.close()


def rpc(port, token, method, params=None, extra=None):
    status, headers, raw = request(port, "/mcp", token,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            {"MCP-Protocol-Version": PROTOCOL, **(extra or {})})
    if status != 200:
        return status, headers, raw
    if raw.startswith("event:") or raw.startswith("data:"):
        raw = next(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
    return status, headers, json.loads(raw)


class FaultApi(http.server.BaseHTTPRequestHandler):
    """Test-only upstream. Real API authentication is verified separately, without mocks."""
    mode = "500"
    seen = []
    entered = threading.Event()
    idle = threading.Event()
    lock = threading.Lock()
    inflight = 0
    peak = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        with self.__class__.lock:
            self.__class__.inflight += 1
            self.__class__.peak = max(self.__class__.peak, self.__class__.inflight)
            self.__class__.idle.clear()
        try:
            self.respond()
        finally:
            with self.__class__.lock:
                self.__class__.inflight -= 1
                if self.__class__.inflight == 0:
                    self.__class__.idle.set()

    def respond(self):
        self.__class__.seen.append((self.command, self.path, dict(self.headers)))
        mode = self.__class__.mode
        self.__class__.entered.set()
        if mode == "slow-headers":
            time.sleep(6)
        if mode == "disconnect":
            self.connection.close()
            return
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/should-never-be-followed")
            self.end_headers()
            return
        status = int(mode) if mode in {"401", "403", "404", "500"} else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.flush()
        if mode == "slow-body":
            time.sleep(6)
        if mode == "concurrent":
            time.sleep(0.3)
        if mode == "large":
            body = SECRET * 1000
        elif mode == "wrong-id":
            body = json.dumps({**profile("alice"), "user": {"id":"alice","name":"Test","description":"Test","roleIds":["unassigned-role"]}})
        elif mode in {"slow-headers", "slow-body", "concurrent", "valid"}:
            body = json.dumps(profile(claims(self.headers["Authorization"].split()[1])["sub"]))
        elif mode == "extra-field":
            body = json.dumps({**profile(claims(self.headers["Authorization"].split()[1])["sub"]), "internalNote": SECRET})
        else:
            body = SECRET
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body.encode())


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        REPORT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='access-credentials-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.credentials = Path(cls.temp.name)
        subprocess.run(['java', str(ROOT/'tools/DemoCredentials.java'), str(cls.credentials), '--fixtures'], check=True)
        cls.tokens = {p.stem: p.read_text().strip() for p in cls.credentials.glob('*.jwt')}
        cls.processes = []
        cls.api = cls.start('api-server', 'api', '--spring.profiles.active=' + API_PROFILE)
        cls.second_api = cls.start('api-server', 'api-restart', '--spring.profiles.active=' + API_PROFILE)
        cls.mcp = cls.start('mcp-server', 'mcp', f'--access.base-url=http://127.0.0.1:{cls.api}')
        cls.single = cls.start('mcp-server', 'single', '--server.tomcat.threads.max=1', '--server.tomcat.threads.min-spare=1', f'--access.base-url=http://127.0.0.1:{cls.api}')
        cls.fault_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FaultApi)
        cls.addClassCleanup(cls.fault_server.server_close)
        cls.addClassCleanup(cls.fault_server.shutdown)
        threading.Thread(target=cls.fault_server.serve_forever, daemon=True).start()
        cls.fault = cls.start('mcp-server', 'fault', f'--access.base-url=http://127.0.0.1:{cls.fault_server.server_port}')
        cls.refresh_tokens()

    @classmethod
    def refresh_tokens(cls):
        before = {name:(cls.credentials/name).read_bytes() for name in ['issuer-public.pem','delegation-public.pem']}
        subprocess.run(['java',str(ROOT/'tools/DemoCredentials.java'),str(cls.credentials),'--refresh-fixtures'],
                       check=True,stdout=subprocess.DEVNULL)
        if before != {name:(cls.credentials/name).read_bytes() for name in before}:
            raise AssertionError('Fixture refresh changed a server verification key')
        cls.tokens = {p.stem:p.read_text().strip() for p in cls.credentials.glob('*.jwt')}

    @classmethod
    def start(cls, module, name, *args):
        port = free_port()
        log = (REPORT/f'{name}.log').open('w')
        cls.addClassCleanup(log.close)
        process = subprocess.Popen(['java', '-jar', str(ROOT/module/'build/libs'/f'{module}-0.1.0.jar'),
            f'--server.port={port}', '--spring.main.banner-mode=off', *args], cwd=ROOT,
            env={**os.environ, 'DEMO_DIR': str(cls.credentials)}, stdout=log, stderr=subprocess.STDOUT)
        def stop():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        cls.addClassCleanup(stop)
        cls.processes.append((stop, log))
        deadline = time.monotonic()+40
        while time.monotonic() < deadline:
            if process.poll() is not None: raise RuntimeError(f'{name} failed; see build/integration/{name}.log')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2): return port
            except OSError: time.sleep(.1)
        raise RuntimeError(f'{name} failed to bind in 40 seconds')

    def call(self, name='get_my_access', args=None, user='alice', token=None, port=None, extra=None):
        return rpc(port or self.mcp, self.tokens['mcp-'+user] if token is None else token,
            'tools/call', {'name': name, 'arguments': {} if args is None else args}, extra)

    def result(self, response):
        status, _, payload = response
        self.assertEqual(status, 200)
        self.assertFalse(payload['result']['isError'], payload['result']['content'])
        value = json.loads(payload['result']['content'][0]['text'])
        self.assertEqual(value, payload['result']['structuredContent'])
        return value

    def error(self, response, code):
        status, _, payload = response
        self.assertEqual(status, 200)
        self.assertTrue(payload['result']['isError'])
        self.assertEqual(payload['result']['content'], [{'type':'text','text':code}])

    def test_01_initialize_tools_and_three_users(self):
        self.refresh_tokens()
        status, headers, payload = rpc(self.mcp, self.tokens['mcp-alice'], 'initialize', {
            'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name':'verifier','version':'1'}})
        self.assertEqual(status, 200)
        self.assertEqual(payload['result']['protocolVersion'], PROTOCOL)
        self.assertFalse(any(k.lower()=='mcp-session-id' for k in headers))
        self.assertEqual(request(self.mcp, '/mcp', self.tokens['mcp-alice'], {'jsonrpc':'2.0','method':'notifications/initialized'})[0],202)
        tools = rpc(self.mcp,self.tokens['mcp-alice'],'tools/list')[2]['result']['tools']
        self.assertEqual({t['name'] for t in tools}, {'get_my_access','check_my_resource_access','explain_authorization','list_access_entities','get_access_entity','get_requestable_roles','assign_role_to_user','remove_role_from_user','attach_policy_to_role','detach_policy_from_role','attach_resource_to_policy','detach_resource_from_policy',
                'list_my_pages','list_my_page_endpoints','list_my_endpoint_fields','check_my_context_access'})
        for tool in tools:
            write=tool['name'].startswith(('assign_','remove_','attach_','detach_'))
            self.assertEqual(tool['annotations']['readOnlyHint'],not write)
            self.assertEqual(tool['annotations']['destructiveHint'],write)
            self.assertTrue(tool['annotations']['idempotentHint'])
        for tool in tools: self.assertFalse(tool['inputSchema']['additionalProperties'])
        schemas = {t['name']:t['inputSchema'] for t in tools}
        self.assertEqual(schemas['get_my_access']['properties'],{})
        self.assertEqual(schemas['check_my_resource_access']['properties']['resourceId']['pattern'],'^R[0-9]{3}$')
        self.assertEqual(schemas['explain_authorization']['properties']['concept']['enum'],['overview','permission','role','policy','resource'])
        for user, resources in [('alice',['R100','R200','R300']),('bob',['R100','R400']),('carol',[]),('local-id-demo',[])]:
            value = self.result(self.call(user=user))
            self.assertEqual(value['user']['id'],'U900' if user=='local-id-demo' else user)
            self.assertEqual([r['id'] for r in value['resources']], resources)
            api_token = self.tokens['api-valid' if user=='alice' else 'api-'+user]
            for port in [self.api,self.second_api]:
                status,_,raw = request(port,'/api/me',api_token)
                self.assertEqual(status,200); self.assertEqual(json.loads(raw),value)
            if user=='carol': self.assertEqual([value[k] for k in ['roles','policies','resources']],[[],[],[]])

    def test_02_delegation_and_expiry(self):
        self.refresh_tokens()
        FaultApi.mode='valid'; FaultApi.seen.clear()
        for fixture,user in [('mcp-alice','alice'),('mcp-bob','bob'),('mcp-manipulated','carol'),('mcp-near-expiry','alice')]:
            original = self.tokens[fixture]
            self.assertGreater(claims(original)['exp'],time.time())
            if fixture=='mcp-near-expiry': self.assertLess(claims(original)['exp']-time.time(),60)
            self.assertEqual(self.result(self.call(port=self.fault,token=original))['user']['id'],user)
            method,path,headers = FaultApi.seen[-1]
            self.assertEqual((method,path),('GET','/api/me'))
            delegated = headers['Authorization'].split()[1]
            self.assertTrue(delegated != original,'Client token must not pass through')
            actual = claims(delegated)
            self.assertEqual(actual['sub'],user)
            self.assertEqual(actual['iss'],'https://demo-delegation.invalid')
            self.assertIn(actual['aud'], ['access-api', ['access-api']])
            self.assertEqual(actual['act'],{'sub':'access-mcp'})
            self.assertEqual(actual['original_iss'],'https://demo-issuer.invalid')
            self.assertEqual(actual['scope'],'access.read')
            self.assertLessEqual(actual['exp']-actual['iat'],60)
            self.assertLessEqual(actual['exp'],claims(original)['exp'])
            self.assertNotIn('grants',actual); self.assertNotIn('X-User-Id',headers)
            self.assertEqual(request(self.api,'/api/me',delegated)[0],200)
            self.assertEqual(rpc(self.mcp,delegated,'tools/list')[0],401)

    def test_03_jwt_validation_and_identity_binding(self):
        self.refresh_tokens()
        for target,port,path in [('mcp',self.mcp,'/mcp'),('api',self.api,'/api/me')]:
            for fixture in [None,'wrong-audience','wrong-issuer','expired','future','forged','wrong-alg','no-exp','no-iss','no-aud','no-sub','blank-subject','typed-subject']:
                with self.subTest(target=target,fixture=fixture):
                    token=self.tokens[f'{target}-{fixture}'] if fixture else None
                    if token:
                        raw=claims(token); now=time.time()
                        if fixture not in ['expired','no-exp']: self.assertGreater(raw['exp'],now,'Negative token expired before its intended validation')
                        if fixture=='expired': self.assertLess(raw['exp'],now)
                        if fixture=='future': self.assertGreater(raw['nbf'],now)
                    self.assertEqual(request(port,path,token)[0],401)
            token=self.tokens[target+'-no-nbf']
            self.assertEqual(rpc(port,token,'tools/list')[0] if target=='mcp' else request(port,path,token)[0],200)
        for fixture in ['wrong-actor','typed-actor','no-actor','wrong-original','typed-original','no-original','long-lived','original-case','original-space']:
            self.assertEqual(request(self.api,'/api/me',self.tokens['api-'+fixture])[0],401)
        self.assertEqual(request(self.api,'/api/me',self.tokens['mcp-alice'])[0],401)
        for suffix in ['unknown','sub-case','sub-accent','sub-space']:
            status,_,raw=request(self.api,'/api/me',self.tokens['api-'+suffix])
            self.assertEqual(status,403); self.assertEqual(json.loads(raw),{'code':'FORBIDDEN'})
            self.error(self.call(token=self.tokens['mcp-'+suffix]),'UPSTREAM_ACCESS_DENIED')

    def test_04_scope_is_not_domain_permission(self):
        self.refresh_tokens()
        self.assertEqual(rpc(self.mcp,self.tokens['mcp-no-scope'],'tools/list')[0],403)
        limited=self.tokens['mcp-connect-only']
        self.assertEqual(rpc(self.mcp,limited,'tools/list')[0],200)
        for tool,args in [('get_my_access',{}),('check_my_resource_access',{'resourceId':'R100'}),('explain_authorization',{'concept':'role'})]:
            self.error(self.call(name=tool,args=args,token=limited),'FORBIDDEN')
        self.assertEqual(request(self.api,'/api/me',self.tokens['api-no-scope'])[0],403)
        self.assertEqual(self.result(self.call(token=self.tokens['mcp-manipulated']))['resources'],[])
        self.assertEqual(self.result(self.call(user='carol',extra={'X-User-Id':'alice'}))['user']['id'],'carol')
        self.assertEqual(json.loads(request(self.api,'/api/me',self.tokens['api-carol'],extra={'X-User-Id':'alice'})[2])['resources'],[])

    def test_05_provenance_and_denial_equivalence(self):
        self.refresh_tokens()
        for user in ['alice','bob']:
            all_access=self.result(self.call(user=user))
            role_ids={r['id'] for r in all_access['roles']}
            policy_ids={p['id'] for p in all_access['policies']}
            self.assertEqual(len(role_ids),len(all_access['roles']))
            self.assertEqual(len(policy_ids),len(all_access['policies']))
            for p in all_access['policies']:
                self.assertTrue(set(p['grantedByRoleIds']) <= role_ids)
            for r in all_access['resources']:
                pairs={(g['roleId'],g['policyId']) for g in r['grants']}
                self.assertEqual(len(pairs),len(r['grants']))
                self.assertTrue({p[0] for p in pairs} <= role_ids)
                checked=self.result(self.call(name='check_my_resource_access',args={'resourceId':r['id']},user=user))
                self.assertEqual(checked,{'resourceId':r['id'],'allowed':True,'resource':{k:r[k] for k in ['id','name','description']},'grants':r['grants']})
            shared=next(p for p in all_access['policies'] if p['id']=='policy-shared')
            self.assertEqual(shared['grantedByRoleIds'],['role-analyst','role-editor'] if user=='alice' else ['role-billing'])
        for resource in ['R400','R999']:
            denied=self.result(self.call(name='check_my_resource_access',args={'resourceId':resource}))
            self.assertEqual(denied,{'resourceId':resource,'allowed':False,'resource':None,'grants':[]})
        alice=self.result(self.call())
        self.assertEqual(len(next(r for r in alice['resources'] if r['id']=='R100')['grants']),3)

    def test_06_input_and_concepts(self):
        self.refresh_tokens()
        for args in [{'userId':'bob'},{'url':'http://127.0.0.1'},{'roleId':'role-billing'}]: self.error(self.call(args=args),'INVALID_ARGUMENT')
        for resource in [None,100,['R100'],'../R100','R100\n','R'*5000]:
            self.error(self.call(name='check_my_resource_access',args={'resourceId':resource}),'INVALID_ARGUMENT')
        self.error(self.call(name='check_my_resource_access',args={'resourceId':'R100','userId':'bob'}),'INVALID_ARGUMENT')
        for concept in ['overview','permission','role','policy','resource']:
            data=self.result(self.call(name='explain_authorization',args={'concept':concept}))
            self.assertEqual(set(data),{'concept','explanation'}); self.assertEqual(data['concept'],concept)
        self.error(self.call(name='explain_authorization',args={'concept':'admin'}),'INVALID_ARGUMENT')
        self.assertEqual(request(self.api,'/api/me?userId=bob',self.tokens['api-valid'])[0],400)
        self.assertEqual(request(self.api,'/api/me/resources/bad/access',self.tokens['api-valid'])[0],400)

    def test_07_origin_host_and_static_policy(self):
        self.refresh_tokens()
        for body in [None,{'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}}]:
            for origin in ['https://evil.invalid','null',f'http://localhost:{self.mcp}',f'http://127.0.0.1:{self.mcp+1}']:
                self.assertEqual(request(self.mcp,'/mcp',self.tokens['mcp-alice'],body,{'Origin':origin})[0],403)
            for host in ['evil.invalid','127.0.0.1:1']:
                self.assertEqual(request(self.mcp,'/mcp',self.tokens['mcp-alice'],body,{'Host':host})[0],403)
        origin={'Origin':f'http://127.0.0.1:{self.mcp}'}
        self.assertEqual(self.result(self.call(extra=origin))['user']['id'],'alice')
        self.assertEqual(self.call(token='',extra=origin)[0],401)
        self.assertEqual(request(self.api,'/api/me',self.tokens['api-valid'],extra={'Origin':f'http://127.0.0.1:{self.api}'})[0],403)
        for path in ['/chat.html','/chat.js','/chat.css']:
            status,headers,_=request(self.mcp,path)
            self.assertEqual(status,200); self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
            self.assertEqual(request(self.mcp,path,extra={'Origin':f'http://127.0.0.1:{self.mcp}'})[0],200)
            self.assertEqual(request(self.mcp,path,extra={'Origin':'https://evil.invalid'})[0],403)
            self.assertEqual(request(self.mcp,path,extra={'Origin':f'http://127.0.0.1:{self.mcp}','Host':'127.0.0.1:1'})[0],403)
            self.assertEqual(request(self.api,path)[0],401)
            self.assertEqual(request(self.mcp,path,method='POST')[0],401)
        for path in ['/delegation-private.pem','/issuer-public.pem','/mcp-alice.jwt','/application.properties','/README.md','/tests/chat.test.mjs']:
            self.assertEqual(request(self.mcp,path)[0],401)

    def test_08_context_isolation(self):
        self.refresh_tokens()
        barrier=threading.Barrier(8)
        def invoke(i):
            barrier.wait(timeout=10)
            if i%4==0: self.assertEqual(self.call(token='')[0],401)
            elif i%4==1: self.error(self.call(token=self.tokens['mcp-connect-only']),'FORBIDDEN')
            else:
                user='alice' if i%4==2 else 'bob'
                self.assertEqual(self.result(self.call(user=user))['user']['id'],user)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(invoke,range(32)))
        for user in ['alice','bob','carol','alice']:
            self.assertEqual(self.result(self.call(user=user,port=self.single))['user']['id'],user)
            self.error(self.call(port=self.single,token=self.tokens['mcp-connect-only']),'FORBIDDEN')
            self.assertEqual(self.call(port=self.single,token='')[0],401)
        self.assertEqual(self.call(token='',extra={'Mcp-Session-Id':'arbitrary'})[0],401)

    def test_09_protocol_encoded_paths_and_limits(self):
        self.refresh_tokens()
        token=self.tokens['mcp-alice']; marker=SECRET+'\nFORGED_LOG_LINE'
        for path in ['/mcp','/%6dcp','/m%63p']:
            for body,expected in [(b'{"secret":"'+marker.encode(),400),({'jsonrpc':'2.0','id':71,'method':marker},500)]:
                status,_,raw=request(self.mcp,path,token,body)
                self.assertEqual(status,expected)
                self.assertEqual(json.loads(raw),{'code':'MCP_REQUEST_REJECTED'})
            status,_,raw=request(self.mcp,path,token,{'jsonrpc':'2.0','method':'notifications/'+marker})
            self.assertEqual((status,raw),(202,''))
            status,_,raw=request(self.mcp,path,token,{'jsonrpc':'2.0','id':72,'method':'tools/call','params':{'name':marker}})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw),{'jsonrpc':'2.0','id':72,'error':{'code':-32603,'message':'MCP_REQUEST_FAILED'}})
            status,_,raw=request(self.mcp,path,token,{'jsonrpc':'2.0','id':73,'method':'tools/call','params':{'name':'get_my_access','arguments':None}})
            self.assertEqual(status,200); self.error((status,{},json.loads(raw)),'INVALID_ARGUMENT')
            self.assertEqual(json.loads(raw)['id'],73)
            payload=json.dumps({'jsonrpc':'2.0','id':74,'method':'tools/call','params':{'name':'get_my_access','arguments':{}}}).encode()
            exact=payload+b' '*(16384-len(payload))
            self.assertEqual(request(self.mcp,path,token,exact)[0],200)
            before=len(FaultApi.seen)
            for body,chunked in [(exact+b' ',False),([exact,b' '],True)]:
                status,_,raw=request(self.fault,path,token,body,chunked=chunked)
                self.assertEqual(status,413); self.assertEqual(json.loads(raw),{'code':'MCP_REQUEST_REJECTED'})
            self.assertEqual(len(FaultApi.seen),before)
        for version in [PROTOCOL,marker]:
            status,_,data=rpc(self.mcp,token,'initialize',{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':marker,'version':marker}})
            self.assertEqual(status,200); self.assertNotIn(SECRET,json.dumps(data))

    def test_10_downstream_failures_and_limits(self):
        self.refresh_tokens()
        for mode,code in [('500','UPSTREAM_UNAVAILABLE'),('401','UPSTREAM_ACCESS_DENIED'),('403','UPSTREAM_ACCESS_DENIED'),
                ('redirect','UPSTREAM_UNAVAILABLE'),('large','UPSTREAM_INVALID_RESPONSE'),('wrong-id','UPSTREAM_INVALID_RESPONSE'),
                ('malformed','UPSTREAM_UNAVAILABLE'),('disconnect','UPSTREAM_UNAVAILABLE'),('slow-headers','UPSTREAM_UNAVAILABLE'),('slow-body','UPSTREAM_UNAVAILABLE')]:
            with self.subTest(mode=mode):
                FaultApi.mode=mode; FaultApi.seen.clear(); started=time.monotonic()
                response=self.call(port=self.fault)
                self.error(response,code); self.assertLess(time.monotonic()-started,3.5)
                self.assertNotIn(SECRET,json.dumps(response))
                self.assertTrue(FaultApi.seen)
                for method,path,headers in FaultApi.seen: self.assertEqual((method,path),('GET','/api/me'))
                self.assertTrue(FaultApi.idle.wait(8))
        FaultApi.mode='extra-field'
        self.assertNotIn(SECRET,json.dumps(self.result(self.call(port=self.fault))))

    def test_11_disconnect_and_overlap(self):
        self.refresh_tokens()
        FaultApi.mode='slow-headers'; FaultApi.entered.clear()
        body=json.dumps({'jsonrpc':'2.0','id':9,'method':'tools/call','params':{'name':'get_my_access','arguments':{}}}).encode()
        wire=(f'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{self.fault}\r\nAuthorization: Bearer {self.tokens["mcp-alice"]}\r\n'
              f'Accept: application/json, text/event-stream\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()+body
        with socket.create_connection(('127.0.0.1',self.fault)) as sock:
            sock.sendall(wire); self.assertTrue(FaultApi.entered.wait(3))
        self.error(self.call(port=self.fault,token=self.tokens['mcp-connect-only']),'FORBIDDEN')
        self.assertEqual(self.call(port=self.fault,token='')[0],401)
        self.assertTrue(FaultApi.idle.wait(8))
        self.error(self.call(port=self.fault,token=self.tokens['mcp-connect-only']),'FORBIDDEN')
        FaultApi.mode='concurrent'; FaultApi.peak=0; barrier=threading.Barrier(4)
        def invoke(i):
            barrier.wait(timeout=5); user='alice' if i%2==0 else 'bob'
            self.assertEqual(self.result(self.call(port=self.fault,user=user))['user']['id'],user)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(invoke,range(4)))
        self.assertGreaterEqual(FaultApi.peak,2)

    def test_12_packaged_allowlist_and_source_layout(self):
        self.refresh_tokens()
        with zipfile.ZipFile(ROOT/'mcp-server/build/libs/mcp-server-0.1.0.jar') as jar:
            static={p for p in jar.namelist() if p.startswith('BOOT-INF/classes/static/') and not p.endswith('/')}
            self.assertEqual(static,{f'BOOT-INF/classes/static/chat.{ext}' for ext in ['html','js','css']})
            self.assertFalse(any(p.endswith(('.pem','.jwt','.env')) for p in jar.namelist()))
        self.assertEqual(list((ROOT/'mcp-server/src/main/resources/static').rglob('*')), [])
        for name in ['mcp-server','api-server','security-support','build.gradle','gradlew']:
            self.assertFalse((ROOT.parent/name).exists())

    def test_13_local_database_failure_has_no_fixture_fallback(self):
        self.refresh_tokens()
        for profiles in ['local', 'local,fixture']:
            label = 'local-unavailable' if profiles == 'local' else 'mixed-profiles'
            with (REPORT/f'{label}.log').open('w') as log:
                process = subprocess.Popen(['java', '-jar', str(ROOT/'api-server/build/libs/api-server-0.1.0.jar'),
                    '--server.port=0', '--spring.profiles.active='+profiles,
                    f'--spring.datasource.url=jdbc:mysql://127.0.0.1:{free_port()}/unavailable?connectTimeout=300&socketTimeout=300',
                    '--spring.datasource.hikari.connection-timeout=300', '--spring.flyway.connect-retries=0'],
                    cwd=ROOT, env={**os.environ, 'DEMO_DIR':str(self.credentials), 'MYSQL_PASSWORD':'unused-local-test'},
                    stdout=log, stderr=subprocess.STDOUT)
                try:
                    self.assertNotEqual(process.wait(timeout=20), 0)
                finally:
                    if process.poll() is None: process.kill(); process.wait(timeout=5)
            output=(REPORT/f'{label}.log').read_text()
            self.assertNotIn('Started ApiApplication', output)

    def test_14_catalog_requestable_and_management_scopes(self):
        self.refresh_tokens()
        for user,expected in [('alice',['alice']),('bob',['bob']),('carol',['carol']),('local-id-demo',['U900'])]:
            data=self.result(self.call(name='list_access_entities',args={'entity':'users'},user=user))
            self.assertEqual([item['id'] for item in data['items']],expected)
        admin=self.tokens['mcp-admin-read-only']
        all_users=self.result(self.call(name='list_access_entities',args={'entity':'users'},token=admin))
        self.assertEqual([item['id'] for item in all_users['items']],['U900','admin','alice','bob','carol'])
        for id in ['bob','no-such-user']:
            self.error(self.call(name='get_access_entity',args={'entity':'users','id':id}),'NOT_FOUND')
        for user,expected in [('alice',['role-billing']),('bob',['role-analyst','role-editor']),('carol',['role-analyst','role-billing','role-editor'])]:
            data=self.result(self.call(name='get_requestable_roles',user=user))
            self.assertEqual([r['id'] for r in data['roles']],expected)
            for r in data['roles']: self.assertEqual(set(r),{'id','name','description'})
        for name,args in [('get_my_access',{}),('check_my_resource_access',{'resourceId':'R100'}),('explain_authorization',{'concept':'role'}),('list_access_entities',{'entity':'roles'}),('get_access_entity',{'entity':'roles','id':'role-billing'}),('get_requestable_roles',{})]:
            self.error(self.call(name=name,args=args,token=self.tokens['mcp-admin-manage-only']),'FORBIDDEN')
        operations=[('assign_role_to_user',{'userId':'carol','roleId':'role-billing'}),
                    ('remove_role_from_user',{'userId':'carol','roleId':'role-billing'}),
                    ('attach_policy_to_role',{'roleId':'role-billing','policyId':'policy-edit'}),
                    ('detach_policy_from_role',{'roleId':'role-billing','policyId':'policy-edit'}),
                    ('attach_resource_to_policy',{'policyId':'policy-billing','resourceId':'R300'}),
                    ('detach_resource_from_policy',{'policyId':'policy-billing','resourceId':'R300'})]
        for name,args in operations:
            self.error(self.call(name=name,args=args,token=admin),'FORBIDDEN')
            self.error(self.call(name=name,args=args,token=self.tokens['mcp-alice-manage']),'FORBIDDEN')
            self.error(self.call(name=name,args=args,token=self.tokens['mcp-unknown-manage']),'FORBIDDEN')
        protected=[('assign_role_to_user',{'userId':'carol','roleId':'SYSTEM_ADMIN'}),
          ('remove_role_from_user',{'userId':'admin','roleId':'SYSTEM_ADMIN'}),
          ('attach_policy_to_role',{'roleId':'SYSTEM_ADMIN','policyId':'policy-edit'}),
          ('attach_policy_to_role',{'roleId':'role-billing','policyId':'SYSTEM_POLICY'}),
          ('attach_resource_to_policy',{'policyId':'SYSTEM_POLICY','resourceId':'R300'}),
          ('attach_resource_to_policy',{'policyId':'policy-billing','resourceId':'R900'})]
        for name,args in protected:
            self.error(self.call(name=name,args=args,user='admin'),'FORBIDDEN')
            reverse={'assign_role_to_user':'remove_role_from_user',
                     'attach_policy_to_role':'detach_policy_from_role',
                     'attach_resource_to_policy':'detach_resource_from_policy'}.get(name,name)
            self.error(self.call(name=reverse,args=args,user='admin'),'FORBIDDEN')
        for method in ['PUT','DELETE']:
            for extra in [{'Origin':f'http://127.0.0.1:{self.api}'},{'Host':'evil.invalid'}]:
                self.assertEqual(request(self.api,'/api/manage/users/carol/roles/role-billing',self.tokens['api-admin-manage'],extra=extra,method=method)[0],403)
            self.assertEqual(request(self.api,'/api/manage/users/carol/roles/role-billing',self.tokens['api-admin-read'],method=method)[0],403)
        self.assertEqual(request(self.api,'/api/me',self.tokens['api-admin-manage'])[0],403)
        for token in ['api-alice-manage','api-unknown-manage']:
            status,_,raw=request(self.api,'/api/manage/users/unknown/roles/unknown?extra=ignored',self.tokens[token],body={'userId':'admin'},method='PUT')
            self.assertEqual(status,403); self.assertEqual(json.loads(raw),{'code':'FORBIDDEN'})
        self.assertEqual(request(self.api,'/api/manage/users/carol/roles/role-billing?extra=ignored',self.tokens['api-admin-manage'],method='PUT')[0],400)

    def test_15_fixture_management_state_transitions(self):
        self.refresh_tokens()
        if API_PROFILE!='fixture': self.skipTest('Actual MySQL mutations are TESTER-only in an isolated v2 database')
        admin=self.tokens['mcp-admin-manage-only']
        pairs=[('assign_role_to_user','remove_role_from_user',{'userId':'carol','roleId':'role-billing'}),
               ('attach_policy_to_role','detach_policy_from_role',{'roleId':'role-billing','policyId':'policy-edit'}),
               ('attach_resource_to_policy','detach_resource_from_policy',{'policyId':'policy-billing','resourceId':'R300'})]
        for add,remove,targets in pairs:
            try:
                first=self.result(self.call(name=add,args=targets,token=admin))
                self.assertEqual(first['relationship'],targets); self.assertTrue(first['changed']); self.assertTrue(first['present'])
                again=self.result(self.call(name=add,args=targets,token=admin)); self.assertFalse(again['changed']); self.assertTrue(again['present'])
                gone=self.result(self.call(name=remove,args=targets,token=admin)); self.assertTrue(gone['changed']); self.assertFalse(gone['present'])
                absent=self.result(self.call(name=remove,args=targets,token=admin)); self.assertFalse(absent['changed']); self.assertFalse(absent['present'])
            finally: self.call(name=remove,args=targets,token=admin)
        targets={'userId':'carol','roleId':'role-billing'}
        try:
            self.result(self.call(name='assign_role_to_user',args=targets,token=admin))
            self.assertEqual([r['id'] for r in self.result(self.call(user='carol'))['resources']],['R100','R400'])
            self.assertNotIn('role-billing',[r['id'] for r in self.result(self.call(name='get_requestable_roles',user='carol'))['roles']])
        finally: self.call(name='remove_role_from_user',args=targets,token=admin)
        self.assertEqual(self.result(self.call(user='carol'))['resources'],[])

    def test_16_exact_mysql_snapshot_parser(self):
        from verify_mysql import parse_snapshot, EXPECTED_COUNTS, EXPECTED_HISTORY
        text=''.join(f'{version}\t{script}\t123\t1\n' for version,script in EXPECTED_HISTORY)
        text+=''.join(f'{name}\t{count}\n' for name,count in EXPECTED_COUNTS.items())
        self.assertEqual(parse_snapshot(text)['counts'],EXPECTED_COUNTS)
        # roles must not match the suffix of user_roles or a different count.
        with self.assertRaises(ValueError): parse_snapshot(text.replace('\nroles\t4\n','\nroles\t99\n'))
        with self.assertRaises(ValueError): parse_snapshot(text.replace('\nroles\t4\n','\n'))
        with self.assertRaises(ValueError): parse_snapshot(text.replace('\t123\t1\n','\t123\t0\n',1))

    def test_99_no_secrets_in_logs_after_shutdown(self):
        self.refresh_tokens()
        self.assertTrue(FaultApi.idle.wait(8))
        for stop,log in self.processes: stop(); log.flush()
        for path in REPORT.glob('*.log'):
            log=path.read_text(); self.assertNotIn(SECRET,log)
            self.assertNotIn('PRIVATE KEY',log)
            for token in self.tokens.values(): self.assertNotIn(token,log)
            # Catch dynamically issued downstream JWTs, not just fixture tokens.
            import re
            self.assertIsNone(re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',log))

if __name__=='__main__':
    REPORT.mkdir(parents=True,exist_ok=True)
    with (REPORT/'results.txt').open('w') as report:
        class Tee:
            def write(self,text):
                import sys
                sys.stderr.write(text); report.write(text)
            def flush(self): report.flush()
        print('Integration API profile: '+API_PROFILE)
        result=unittest.TextTestRunner(stream=Tee(),verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Integration))
    raise SystemExit(0 if result.wasSuccessful() else 1)
