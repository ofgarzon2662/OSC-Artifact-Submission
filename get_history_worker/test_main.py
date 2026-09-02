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


def test_history_limit_clamped_to_max_page_size(monkeypatch):
    """Requesting a limit beyond MAX_PAGE_SIZE should be clamped."""
    fake = [
        {"txId": f"tx{i}", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False}
        for i in range(10)
    ]

    async def fake_fetch(_):
        return fake

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000004'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)
    original_max = ghw.MAX_PAGE_SIZE
    ghw.MAX_PAGE_SIZE = 5
    try:
        r = client.get('/history', params={'artifactId': aid, 'offset': 0, 'limit': 1000})
        assert r.status_code == 200
        data = r.json()
        # Limit is clamped to MAX_PAGE_SIZE=5; only 5 items should be returned
        assert len(data['items']) == 5
        assert data['total'] == 10
    finally:
        ghw.MAX_PAGE_SIZE = original_max


def test_history_offset_equals_total_returns_empty(monkeypatch):
    """When offset equals total, items should be empty."""
    fake = [
        {"txId": "tx1", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False},
        {"txId": "tx2", "timestamp": "2025-01-02T00:00:00Z", "isDelete": False},
    ]

    async def fake_fetch(_):
        return fake

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000005'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)
    r = client.get('/history', params={'artifactId': aid, 'offset': 2, 'limit': 10})
    assert r.status_code == 200
    data = r.json()
    assert data['items'] == []
    assert data['total'] == 2
    assert data['hasMore'] is False


def test_history_desc_order_builds_from_asc_on_second_call(monkeypatch):
    """When cache has asc but not desc, desc should be built lazily from asc."""
    fake = [
        {"txId": "t1", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False},
        {"txId": "t2", "timestamp": "2025-01-02T00:00:00Z", "isDelete": False},
        {"txId": "t3", "timestamp": "2025-01-03T00:00:00Z", "isDelete": False},
    ]

    async def fake_fetch(_):
        return fake

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000006'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)

    # First call with asc to populate cache without desc
    r_asc = client.get('/history', params={'artifactId': aid, 'order': 'asc', 'offset': 0, 'limit': 10})
    assert r_asc.status_code == 200
    assert [it['txId'] for it in r_asc.json()['items']] == ['t1', 't2', 't3']

    # Second call with desc — should build desc from cached asc
    r_desc = client.get('/history', params={'artifactId': aid, 'order': 'desc', 'offset': 0, 'limit': 10})
    assert r_desc.status_code == 200
    assert [it['txId'] for it in r_desc.json()['items']] == ['t3', 't2', 't1']


def test_fetch_history_bridge_non_200_raises_502(monkeypatch):
    """fetch_history_from_bridge should raise HTTPException(502) for non-200 bridge responses."""
    import pytest
    import httpx
    from fastapi import HTTPException

    async def fake_fetch_impl(artifact_id: str):
        return await ghw.fetch_history_from_bridge(artifact_id)

    # Patch httpx.AsyncClient.get to return a non-200 response
    class FakeResponse:
        status_code = 503
        text = 'Service Unavailable'
        def json(self):
            return {"message": "unavailable"}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: FakeClient())

    aid = '00000000-0000-4000-8000-000000000007'
    r = client.get('/history', params={'artifactId': aid})
    assert r.status_code == 502


def test_fetch_history_bridge_non_list_response_raises_502(monkeypatch):
    """fetch_history_from_bridge should raise 502 when bridge returns a non-list."""
    import httpx

    class FakeResponseNotList:
        status_code = 200
        text = '{"key": "value"}'
        def json(self):
            return {"key": "value"}  # not a list

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return FakeResponseNotList()

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: FakeClient())
    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000008'
    r = client.get('/history', params={'artifactId': aid})
    assert r.status_code == 502


def test_history_hasmore_true_when_more_items_available(monkeypatch):
    """hasMore should be True when offset+limit < total."""
    fake = [
        {"txId": f"tx{i}", "timestamp": "2025-01-01T00:00:00Z", "isDelete": False}
        for i in range(5)
    ]

    async def fake_fetch(_):
        return fake

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-000000000009'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)
    r = client.get('/history', params={'artifactId': aid, 'offset': 0, 'limit': 2})
    assert r.status_code == 200
    data = r.json()
    assert data['hasMore'] is True
    assert len(data['items']) == 2


def test_refresh_history_post_method(monkeypatch):
    """POST /history/refresh should also work (api_route accepts GET and POST)."""
    second = [{"txId": "new", "timestamp": "2025-01-03Z", "isDelete": False}]

    async def fake_fetch(_):
        return second

    ghw.cache.clear()
    aid = '00000000-0000-4000-8000-00000000000a'
    monkeypatch.setattr(ghw, 'fetch_history_from_bridge', fake_fetch)
    r = client.post('/history/refresh', params={'artifactId': aid})
    assert r.status_code == 200
    data = r.json()
    assert data['total'] == 1
    assert data['artifactId'] == aid


