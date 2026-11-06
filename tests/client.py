"""HTTP helpers for public black-box tests; Python 3.9+, standard library only."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
LAB = json.loads((ROOT / 'lab.json').read_text())['number']
SPEC = json.loads((ROOT / 'contracts/openapi.json').read_text())

class Failure(AssertionError):
    pass

def check(condition, message):
    if not condition:
        raise Failure(message)

def timestamp(value):
    check(isinstance(value, str) and value.endswith('Z'), 'timestamp must be UTC ending in Z')
    try:
        return dt.datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as exc:
        raise Failure('invalid RFC3339 timestamp') from exc

def validate(value, schema, where='response'):
    """Validate the JSON Schema subset used by the supplied contract, not arbitrary OpenAPI."""
    if '$ref' in schema:
        target = SPEC
        for part in schema['$ref'].split('/')[1:]:
            target = target[part]
        return validate(value, target, where)
    kinds = schema.get('type', [])
    if isinstance(kinds, str):
        kinds = [kinds]
    matches = {'null':value is None, 'boolean':isinstance(value,bool), 'integer':isinstance(value,int) and not isinstance(value,bool), 'number':isinstance(value,(int,float)) and not isinstance(value,bool), 'string':isinstance(value,str), 'array':isinstance(value,list), 'object':isinstance(value,dict)}
    if kinds:
        check(any(matches.get(k,False) for k in kinds), where + ': incorrect type')
    if 'enum' in schema:
        check(value in schema['enum'], where + ': invalid enum')
    if 'const' in schema:
        check(value == schema['const'], where + ': invalid const')
    if isinstance(value,dict):
        props = schema.get('properties',{})
        check(all(k in value for k in schema.get('required',[])), where + ': missing required fields')
        if schema.get('additionalProperties') is False:
            check(not set(value).difference(props), where + ': unexpected fields')
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, where + '.' + key)
    if isinstance(value,list):
        check(len(value) >= schema.get('minItems',0), where + ': too few items')
        check(len(value) <= schema.get('maxItems',float('inf')), where + ': too many items')
        if schema.get('uniqueItems'):
            encoded = [json.dumps(x,sort_keys=True) for x in value]
            check(len(encoded)==len(set(encoded)), where + ': duplicate items')
        if 'items' in schema:
            for item in value:
                validate(item, schema['items'], where + '[]')
    if isinstance(value,str):
        check(len(value) >= schema.get('minLength',0), where + ': too short')
        check(len(value) <= schema.get('maxLength',float('inf')), where + ': too long')
        if 'pattern' in schema:
            check(re.search(schema['pattern'], value) is not None, where + ': pattern mismatch')
        if schema.get('format') == 'uuid':
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise Failure(where + ': invalid UUID') from exc
        if schema.get('format') == 'date-time':
            timestamp(value)
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        check(value >= schema.get('minimum',-float('inf')), where + ': below minimum')
        check(value <= schema.get('maximum',float('inf')), where + ': above maximum')
    if 'anyOf' in schema:
        for alternative in schema['anyOf']:
            try:
                validate(value, alternative, where)
                break
            except Failure:
                pass
        else:
            raise Failure(where + ': no anyOf branch matches')

def shape(value, name):
    validate(value, SPEC['components']['schemas'][name])
    return value

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self, base_url=None, ca_file=None, timeout=10):
        self.base = (base_url or os.environ.get('BASE_URL') or ('https://localhost:8443' if LAB==7 else 'http://localhost:8080')).rstrip('/')
        check(self.base.startswith(('http://','https://')), 'BASE_URL must be HTTP(S)')
        check(LAB != 7 or self.base.startswith('https://'), 'lab7 requires HTTPS')
        self.timeout = timeout
        context = ssl.create_default_context(cafile=ca_file or os.environ.get('CA_FILE') or None)
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def call(self, method, path, expected=200, body=None, actor=None, headers=None, raw=None, schema=None):
        h = {'Accept':'application/json'}
        if actor:
            h['X-User-Id' if LAB==1 else 'Authorization'] = actor['id'] if LAB==1 else 'Bearer ' + actor['token']
        if body is not None:
            raw = json.dumps(body,ensure_ascii=False).encode('utf-8')
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=raw, headers=h, method=method)
        start = time.monotonic()
        try:
            response = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            # Do not print URL query, headers, request body, or credentials.
            raise Failure(method + ' ' + path.split('?')[0] + ': connection/TLS failure (' + type(exc).__name__ + ')') from None
        with response:
            status = response.code
            rh = response.headers
            data = response.read(16*1024*1024 + 1)
        check(len(data) <= 16*1024*1024, 'response exceeds test safety limit')
        allowed = [expected] if isinstance(expected,int) else expected
        check(status in allowed, method + ' ' + path.split('?')[0] + ': expected ' + str(allowed) + ', received ' + str(status))
        check(bool(rh.get('X-Request-Id')), 'missing X-Request-Id')
        if LAB>=3:
            check(bool(rh.get('X-Instance-Id')), 'missing X-Instance-Id')
        payload = None
        if schema or status>=400:
            check(rh.get_content_type()=='application/json', 'expected application/json')
            try:
                payload = json.loads(data)
            except (ValueError,UnicodeError):
                raise Failure('invalid response JSON') from None
            shape(payload, 'Error' if status>=400 else schema)
        if status>=400:
            check(payload['error']['request_id']==rh.get('X-Request-Id'),'error request_id differs from header')
            if status==401 and LAB>=2:
                scheme = 'Basic' if path=='/api/v1/auth/sessions' else 'Bearer'
                check(rh.get('WWW-Authenticate','').lower().startswith(scheme.lower()), 'incorrect WWW-Authenticate challenge')
        if status==204:
            check(not data, '204 must not contain body')
        return {'status':status,'headers':rh,'json':payload,'bytes':data,'elapsed':time.monotonic()-start}

    def user(self, label):
        username = 'u_' + label + '_' + uuid.uuid4().hex[:12]
        password = 'T3st-' + uuid.uuid4().hex
        body = {'username':username,'display_name':'Студент ' + label}
        if LAB>=2:
            body['password'] = password
        user = self.call('POST','/api/v1/users',201,body=body,schema='User')['json']
        check(user['username']==username,'username changed')
        actor = {'id':user['id'],'user':user,'registration':body}
        if LAB>=2:
            credential = base64.b64encode((username+':'+password).encode()).decode()
            session = self.call('POST','/api/v1/auth/sessions',201,headers={'Authorization':'Basic '+credential},schema='Session')['json']
            check(session['user']==user,'session user mismatch')
            check(timestamp(session['expires_at']) > dt.datetime.now(dt.timezone.utc),'session already expired')
            actor['token'] = session['token']
        return actor

    def bootstrap(self):
        self.call('GET','/health/live',schema='Health')
        self.call('GET','/health/ready',schema='Health')
        alice, bob, eve = (self.user(label) for label in ('alice','bob','eve'))
        chat = self.call('POST','/api/v1/chats',201,actor=alice,body={'title':'Контрактный тест','member_ids':[alice['id'],bob['id']]},schema='Chat')['json']
        check(set(chat['member_ids'])=={alice['id'],bob['id']},'chat member mismatch')
        return alice,bob,eve,chat

    def message(self, actor, chat, text, key=None, attachments=None):
        body = {'text':text}
        if attachments is not None:
            body['attachment_ids'] = attachments
        headers = {'Idempotency-Key':key or uuid.uuid4().hex} if LAB>=3 else {}
        return self.call('POST','/api/v1/chats/'+chat['id']+'/messages',201,actor=actor,body=body,headers=headers,schema='Message')

    def attachment(self, actor, chat):
        content = (ROOT / 'tests/fixtures/sample.png').read_bytes()
        att = self.call('POST','/api/v1/chats/'+chat['id']+'/attachments?filename=sample.png',201 if LAB==4 else 202,actor=actor,raw=content,headers={'Content-Type':'image/png'},schema='Attachment')['json']
        check(att['status']==('ready' if LAB==4 else 'queued'),'incorrect upload status')
        check(att['chat_id']==chat['id'] and att['owner_id']==actor['id'],'attachment ownership mismatch')
        check(att['sha256']==hashlib.sha256(content).hexdigest() and att['size_bytes']==len(content),'original checksum/size mismatch')
        return att,content

    def wait_ready(self, actor, att, wait_seconds=60):
        deadline = time.monotonic()+wait_seconds
        while time.monotonic()<deadline:
            att = self.call('GET','/api/v1/attachments/'+att['id'],actor=actor,schema='Attachment')['json']
            check(att['status']!='failed','attachment reached failed')
            if att['status']=='ready':
                return att
            time.sleep(0.3)
        raise Failure('attachment did not become ready within test deadline')

def arguments(description, configure=None):
    import argparse
    p = argparse.ArgumentParser(description=description)
    p.add_argument('--base-url', default=None)
    p.add_argument('--ca-file', default=None)
    if configure:
        configure(p)
    return p.parse_args()

def run(main):
    try:
        main()
    except (Failure,ValueError,OSError) as exc:
        print('FAIL:', str(exc))
        raise SystemExit(1)
