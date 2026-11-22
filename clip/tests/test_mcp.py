from fastapi.testclient import TestClient

from clip_service.config import settings
from clip_service.mcp import app


def call(client, name, arguments=None, request_id=1):
    return client.post('/mcp', json={'jsonrpc': '2.0', 'id': request_id, 'name': name,
                                     'arguments': arguments or {}},
                       headers={'X-API-Key': settings.clip_api_key})


def test_mcp_auth_create_status_queue_and_retry(monkeypatch):
    monkeypatch.setattr('clip_service.mcp.run_job.delay', lambda _job_id: None)
    monkeypatch.setattr('clip_service.api.run_job.delay', lambda _job_id: None)
    with TestClient(app) as client:
        assert client.post('/mcp', json={'name': 'get_queue'}).status_code == 401
        source = call(client, 'create_source', {'uri': '/tmp/mcp.mp4'}).json()['result']
        job = call(client, 'create_job', {'source_id': source['id'], 'config': {'count': 1}}).json()['result']
        status = call(client, 'get_job', {'job_id': job['id']}).json()['result']
        assert status['status'] == 'queued'
        assert job['id'] in call(client, 'get_queue').json()['result']['queued']
        retried = call(client, 'retry_export', {'id': job['id']}).json()['result']
        assert retried == {'id': job['id'], 'status': 'queued'}
