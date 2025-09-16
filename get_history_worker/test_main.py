import json
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json().get('status') == 'healthy'


def test_history_validation():
    r = client.get('/history', params={'artifactId': 'not-a-uuid'})
    assert r.status_code == 400


def test_refresh_requires_uuid():
    r = client.get('/history/refresh', params={'artifactId': 'BAD'})
    assert r.status_code == 400


