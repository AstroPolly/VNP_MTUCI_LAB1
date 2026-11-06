"""Isolated boundary tests; never modify the public tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from main import create_app


def user(c, name='alice'):
    r = c.post('/api/v1/users', json={'username': name, 'display_name': name})
    assert r.status_code == 201
    return {'X-User-Id': r.json()['id']}


def test_global_capacity(monkeypatch):
    monkeypatch.setenv('MAX_MESSAGES', '3')
    c = TestClient(create_app())
    h = user(c)
    paths = []
    saved = []
    for _ in range(3):
        r = c.post('/api/v1/chats', headers=h, json={'title': 't', 'member_ids': [h['X-User-Id']]})
        path = '/api/v1/chats/' + r.json()['id'] + '/messages'
        paths.append(path)
        r = c.post(path, headers=h, json={'text': 'keep'})
        assert r.status_code == 201
        saved.append(r.json())
    for path in paths:
        r = c.post(path, headers=h, json={'text': 'reject'})
        assert r.status_code == 503 and r.json()['error']['code'] == 'unavailable'
    assert [c.get(p, headers=h).json()['items'][0] for p in paths] == saved
    assert c.get('/health/ready').status_code == 200


def test_user_chat_limits(monkeypatch):
    monkeypatch.setenv('MAX_USERS', '1')
    monkeypatch.setenv('MAX_CHATS', '1')
    c = TestClient(create_app())
    h = user(c)
    assert c.post('/api/v1/users', json={'username': 'bobby', 'display_name': 'B'}).status_code == 503
    body = {'title': 't', 'member_ids': [h['X-User-Id']]}
    assert c.post('/api/v1/chats', headers=h, json=body).status_code == 201
    assert c.post('/api/v1/chats', headers=h, json=body).status_code == 503


def test_protocol_errors():
    c = TestClient(create_app())
    for path in ['/not-found', '/api/v1/users/me']:
        r = c.get(path)
        assert r.json()['error']['request_id'] == r.headers['X-Request-Id']
    r = c.post('/api/v1/users', content='x', headers={'Content-Type': 'text/plain'})
    assert r.status_code == 415
    r = c.post('/api/v1/users', content='x' * 65537, headers={'Content-Type': 'application/json'})
    assert r.status_code == 413


def test_clock_rollback(monkeypatch):
    from datetime import datetime, timezone
    c = TestClient(create_app())
    h = user(c)
    c.app.state.store.last_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    chat = c.post('/api/v1/chats', headers=h, json={'title': 't', 'member_ids': [h['X-User-Id']]}).json()
    path = '/api/v1/chats/' + chat['id'] + '/messages'
    a = c.post(path, headers=h, json={'text': 'a'}).json()
    b = c.post(path, headers=h, json={'text': 'b'}).json()
    assert a['created_at'] < b['created_at']
    assert c.get(path, headers=h).json()['items'] == [a, b]
