"""Lab1: one process, bounded in-memory state, no persistence."""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger('uvicorn.error')
CODES = {400: 'invalid_request', 401: 'unauthorized', 404: 'not_found',
         409: 'conflict', 413: 'payload_too_large', 415: 'unsupported_media_type',
         429: 'rate_limited', 503: 'unavailable', 500: 'internal_error'}


def positive_env(name, default):
    value = int(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f'{name} must be positive')
    return value


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.users, self.names, self.chats, self.messages = {}, {}, {}, {}
        self.count = 0
        self.max_users = positive_env('MAX_USERS', 10000)
        self.max_chats = positive_env('MAX_CHATS', 10000)
        self.max_messages = positive_env('MAX_MESSAGES', 50000)
        self.rate = positive_env('WRITE_RATE', 1000)
        self.burst = positive_env('WRITE_BURST', 2000)
        self.tokens = float(self.burst)
        self.refill = time.monotonic()
        self.secret = secrets.token_bytes(32)
        self.last_time = datetime.min.replace(tzinfo=timezone.utc)

    def stamp(self):
        # Called under lock: even if wall clock moves backwards, order stays stable.
        self.last_time = max(datetime.now(timezone.utc), self.last_time + timedelta(microseconds=1))
        return self.last_time.isoformat(timespec='microseconds').replace('+00:00', 'Z')

    def write_slot(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.refill) * self.rate)
        self.refill = now
        if self.tokens < 1:
            raise Problem(429, 'Write rate exceeded; retry later')
        self.tokens -= 1

    def page(self, rows, scope, query):
        raw_limit = query.get('limit', '50')
        if not re.fullmatch(r'[0-9]+', raw_limit) or not 1 <= int(raw_limit) <= 100:
            raise Problem(400, 'limit must be an integer from 1 to 100')
        limit = int(raw_limit)
        rows = sorted(rows, key=lambda row: (row['created_at'], row['id']))
        cursor = query.get('cursor')
        if cursor is not None:
            try:
                data, signature = cursor.split('.')
                expected = hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                token_scope, stamp, ident = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
                if token_scope != scope or not any(r['id'] == ident and r['created_at'] == stamp for r in rows):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise Problem(400, 'Invalid cursor') from None
            rows = [r for r in rows if (r['created_at'], r['id']) > (stamp, ident)]
        items = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            data = base64.urlsafe_b64encode(json.dumps([scope, last['created_at'], last['id']]).encode()).decode().rstrip('=')
            next_cursor = data + '.' + hmac.new(self.secret, data.encode(), hashlib.sha256).hexdigest()
        return {'items': items, 'next_cursor': next_cursor}


def canonical_uuid(value, status=400):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise Problem(status, 'Invalid identification' if status == 401 else 'Invalid UUID') from None


def text_field(data, key, maximum):
    value = data[key]
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Problem(400, f'Invalid {key}')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise Problem(400, f'Invalid Unicode in {key}') from None
    return value


def create_app():
    app = FastAPI(title='Lab1 Messenger', docs_url=None, redoc_url=None, openapi_url=None)
    store = Store()
    app.state.store = store
    max_body = positive_env('MAX_BODY_BYTES', 65536)

    def error(request, status, message):
        headers = {'X-Request-Id': request.state.request_id}
        if status == 429:
            headers['Retry-After'] = '1'
        return JSONResponse({'error': {'code': CODES.get(status, 'invalid_request'),
                            'message': message, 'request_id': request.state.request_id}},
                            status_code=status, headers=headers)

    @app.middleware('http')
    async def boundary(request, call_next):
        # Generate our own ID; do not echo untrusted, arbitrarily long header values.
        request.state.request_id = str(uuid.uuid4())
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.error('Unhandled request failure request_id=%s', request.state.request_id)
            response = error(request, 500, 'Internal server error')
        response.headers['X-Request-Id'] = request.state.request_id
        # No body, query, credentials, or user-controlled path in the logs.
        route = request.scope.get('route')
        log.info('method=%s route=%s status=%s elapsed_ms=%.2f request_id=%s',
                 request.method, getattr(route, 'path', 'unmatched'), response.status_code,
                 (time.monotonic() - start) * 1000, request.state.request_id)
        return response

    @app.exception_handler(Problem)
    async def problem(request, exc):
        return error(request, exc.status, exc.message)

    @app.exception_handler(HTTPException)
    async def http_problem(request, exc):
        return error(request, exc.status_code, 'Not found' if exc.status_code == 404 else 'Invalid request')

    def actor(request):
        ident = canonical_uuid(request.headers.get('X-User-Id'), 401)
        if ident not in store.users:
            raise Problem(401, 'Unknown user')
        return ident

    def accessible(chat_id, user_id):
        chat_id = canonical_uuid(chat_id)
        chat = store.chats.get(chat_id)
        if chat is None or user_id not in chat['member_ids']:
            raise Problem(404, 'Chat not found')
        return chat_id

    async def payload(request, fields):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise Problem(415, 'Expected application/json')
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > max_body:
                raise Problem(413, 'Request body too large')
            data.extend(chunk)
        try:
            body = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            raise Problem(400, 'Malformed JSON') from None
        if not isinstance(body, dict) or set(body) != set(fields):
            raise Problem(400, 'Missing or extra fields')
        return body

    @app.get('/health/live')
    @app.get('/health/ready')
    async def health():
        return {'status': 'ok'}

    @app.post('/api/v1/users', status_code=201)
    async def create_user(request: Request):
        body = await payload(request, ['username', 'display_name'])
        name = text_field(body, 'username', 32)
        display = text_field(body, 'display_name', 100)
        if re.fullmatch(r'[a-z0-9_]{3,32}', name) is None:
            raise Problem(400, 'Invalid username')
        with store.lock:
            if name in store.names:
                raise Problem(409, 'Username already exists')
            if len(store.users) >= store.max_users:
                raise Problem(503, 'User capacity reached')
            store.write_slot()
            user = {'id': str(uuid.uuid4()), 'username': name, 'display_name': display, 'created_at': store.stamp()}
            store.users[user['id']] = user
            store.names[name] = user['id']
            return user

    @app.get('/api/v1/users/me')
    async def me(request: Request):
        with store.lock:
            return store.users[actor(request)]

    @app.post('/api/v1/chats', status_code=201)
    async def create_chat(request: Request):
        with store.lock:
            user_id = actor(request)
        body = await payload(request, ['title', 'member_ids'])
        title = text_field(body, 'title', 100)
        members = body['member_ids']
        if not isinstance(members, list) or not 1 <= len(members) <= 100:
            raise Problem(400, 'Invalid member_ids')
        members = [canonical_uuid(m) for m in members]
        with store.lock:
            if len(set(members)) != len(members) or user_id not in members or any(m not in store.users for m in members):
                raise Problem(400, 'Invalid member_ids')
            if len(store.chats) >= store.max_chats:
                raise Problem(503, 'Chat capacity reached')
            store.write_slot()
            chat = {'id': str(uuid.uuid4()), 'title': title, 'member_ids': members, 'created_at': store.stamp()}
            store.chats[chat['id']] = chat
            store.messages[chat['id']] = []
            return chat

    @app.get('/api/v1/chats')
    async def list_chats(request: Request):
        with store.lock:
            user_id = actor(request)
            return store.page([c for c in store.chats.values() if user_id in c['member_ids']], 'chats:' + user_id, request.query_params)

    @app.get('/api/v1/chats/{chat_id}')
    async def get_chat(chat_id: str, request: Request):
        with store.lock:
            return store.chats[accessible(chat_id, actor(request))]

    @app.post('/api/v1/chats/{chat_id}/messages', status_code=201)
    async def send_message(chat_id: str, request: Request):
        with store.lock:
            user_id = actor(request)
            chat_id = accessible(chat_id, user_id)
        body = await payload(request, ['text'])
        content = text_field(body, 'text', 4000)
        with store.lock:
            if store.count >= store.max_messages:
                raise Problem(503, 'Message capacity reached; existing messages are retained')
            store.write_slot()
            message = {'id': str(uuid.uuid4()), 'chat_id': chat_id, 'sender_id': user_id,
                       'text': content, 'created_at': store.stamp()}
            store.messages[chat_id].append(message)
            store.count += 1
            return message

    @app.get('/api/v1/chats/{chat_id}/messages')
    async def list_messages(chat_id: str, request: Request):
        with store.lock:
            chat_id = accessible(chat_id, actor(request))
            return store.page(store.messages[chat_id], 'messages:' + chat_id, request.query_params)

    return app


app = create_app()
