import json
import time
from fastapi.testclient import TestClient
import main as ghw
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


def test_history_happy_path_desc_and_cache(monkeypatch):
    # Prepare fake history (oldest first as Fabric returns)
    fake = [
        {"txId": "tx1", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False, "value": {"id": "a"}},
        {"txId": "tx2", "timestamp": "2025-01-02T00:00:00Z", "isDelete": False, "value": {"id": "b"}},
        {"txId": "tx3", "timestamp": "2025-01-03T00:00:00Z", "isDelete": False, "value": {"id": "c"}},
    ]

    async def fake_fetch(_artifact_id: str):
        return fake

    # Clear cache and patch fetch
    ghw.cache.clear()
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)

    aid = '00000000-0000-4000-8000-000000000001'

    # First call: populates cache, order desc (newest first)
    r1 = client.get('/history', params={
        'artifactId': aid,
        'offset': 0,
        'limit': 2,
        'order': 'desc',
        'includeValue': True
    })
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1['total'] == 3
    # Expect tx3, tx2
    assert [it['txId'] for it in data1['items']] == ['tx3', 'tx2']

    # Second call: from cache, next page
    r2 = client.get('/history', params={
        'artifactId': aid,
        'offset': 2,
        'limit': 2,
        'order': 'desc',
        'includeValue': False
    })
    assert r2.status_code == 200
    data2 = r2.json()
    # Expect only tx1 and without value field
    assert [it['txId'] for it in data2['items']] == ['tx1']
    assert 'value' not in data2['items'][0]


def test_history_order_asc(monkeypatch):
    fake = [
        {"txId": "t1", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False},
        {"txId": "t2", "timestamp": "2025-01-02T00:00:00Z", "isDelete": False},
    ]
    async def fake_fetch(_):
        return fake
    ghw.cache.clear()
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)
    aid = '00000000-0000-4000-8000-000000000002'
    r = client.get('/history', params={'artifactId': aid, 'order': 'asc', 'offset': 0, 'limit': 10})
    assert r.status_code == 200
    items = r.json()['items']
    assert [it['txId'] for it in items] == ['t1', 't2']


def test_refresh_replaces_cache(monkeypatch):
    first = [ {"txId": "a", "timestamp": "2025-01-01Z", "isDelete": False} ]
    second = [ {"txId": "b", "timestamp": "2025-01-02Z", "isDelete": False} ]

    async def fake_fetch_initial(_):
        return first
    async def fake_fetch_next(_):
        return second

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000003'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch_initial)
    r1 = client.get('/history', params={'artifactId': aid})
    assert r1.status_code == 200
    assert r1.json()['total'] == 1

    # Refresh with new data
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch_next)
    rr = client.get('/history/refresh', params={'artifactId': aid})
    assert rr.status_code == 200
    assert rr.json()['total'] == 1

    r2 = client.get('/history', params={'artifactId': aid})
    assert r2.status_code == 200
    assert [it['txId'] for it in r2.json()['items']] == ['b']


