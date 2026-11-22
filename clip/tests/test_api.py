from fastapi.testclient import TestClient
from fastapi import Request, Response

from clip_service.api import app, local_studio_session
from clip_service.config import settings


def test_health_and_auth():
    with TestClient(app) as c:
        assert c.get('/health').status_code == 200
        assert c.post('/sources', json={'uri': 'x'}).status_code == 401
        assert c.post('/sources', json={'uri': 'x'}, headers={'X-API-Key': settings.clip_api_key}).status_code == 200

def test_upload_rejects_non_mp4():
    with TestClient(app) as c:
        response = c.post('/sources/upload', files={'file': ('notes.txt', b'no')},
                          headers={'X-API-Key': settings.clip_api_key})
        assert response.status_code == 415

def test_job_schema_accepts_manual_intervals(monkeypatch):
    monkeypatch.setattr('clip_service.api.run_job.delay', lambda _job_id: None)
    with TestClient(app) as c:
        headers = {'X-API-Key': settings.clip_api_key}
        source = c.post('/sources', json={'uri': '/tmp/example.mp4'}, headers=headers).json()
        response = c.post('/jobs', json={'source_id': source['id'], 'intervals': [{'start': 1, 'end': 20}]}, headers=headers)
        assert response.status_code == 200


def test_studio_connects_without_key_only_on_loopback():
    with TestClient(app) as remote:
        assert remote.get('/studio/session').status_code == 403
        assert remote.get('/jobs').status_code == 401
        remote.cookies.set('clip_studio_key', settings.clip_api_key)
        assert remote.get('/jobs').status_code == 200

    scope = {'type': 'http', 'scheme': 'http', 'server': ('127.0.0.1', 8080),
             'path': '/studio/session', 'query_string': b'',
             'client': ('127.0.0.1', 51234), 'headers': [(b'host', b'127.0.0.1:8080')]}
    response = Response()
    assert local_studio_session(Request(scope), response) == {'ok': True}
    assert 'clip_studio_key=' in response.headers['set-cookie']

    scope['headers'] = [(b'host', b'studio.example')]
    try:
        local_studio_session(Request(scope), Response())
        assert False, 'remote Host header must be rejected'
    except Exception as exc:
        assert exc.status_code == 403
