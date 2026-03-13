import json
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('ADAPTER_API_URL', 'https://osc-api.example.com')
os.environ.setdefault('ADAPTER_API_TOKEN', 'test-token')

import app as adapter_app
from app import app, _build_artifact_body


VALID_SUBMIT_PAYLOAD = {
    'artifactId': 'abc-123',
    'data': {
        'title': 'Test Title',
        'description': 'Test description'
    }
}

VALID_UPDATE_PAYLOAD = {
    'artifactId': 'abc-123',
    'patch': {
        'title': 'Updated Title',
        'description': 'Updated description'
    }
}


def _mock_response(status_code=200, json_data=None, text='', ok=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.ok = (status_code < 400) if ok is None else ok
    mock.text = text
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError('no json')
        mock.text = text
    return mock


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health_returns_200(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['service'], 'adapter')
        self.assertIn('timestamp', data)


class TestSubmit(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_missing_artifact_id_returns_400(self):
        resp = self.client.post(
            '/submit',
            json={'data': {'title': 'T', 'description': 'D'}}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])

    def test_missing_title_returns_400(self):
        resp = self.client.post(
            '/submit',
            json={'artifactId': 'abc-123', 'data': {'description': 'D'}}
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('title', data['error'].lower())

    def test_missing_description_returns_400(self):
        resp = self.client.post(
            '/submit',
            json={'artifactId': 'abc-123', 'data': {'title': 'T'}}
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('description', data['error'].lower())

    @patch('app.requests.post')
    def test_valid_payload_external_success_returns_200(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=200,
            json_data={'success': True, 'artifactId': 'abc-123'}
        )
        resp = self.client.post('/submit', json=VALID_SUBMIT_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])

    @patch('app.requests.post')
    def test_valid_payload_external_failure_returns_502(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=200,
            json_data={'success': False, 'error': 'Chaincode error'}
        )
        resp = self.client.post('/submit', json=VALID_SUBMIT_PAYLOAD)
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])

    @patch('app.requests.post')
    def test_external_api_timeout_returns_502(self, mock_post):
        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.Timeout()
        resp = self.client.post('/submit', json=VALID_SUBMIT_PAYLOAD)
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('timed out', data['error'])

    @patch('app.requests.post')
    def test_submit_calls_new_endpoint(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=200,
            json_data={'success': True}
        )
        self.client.post('/submit', json=VALID_SUBMIT_PAYLOAD)
        call_url = mock_post.call_args[0][0]
        self.assertIn('/v1/artifacts/new', call_url)
        self.assertNotIn('/v1/artifacts/update', call_url)


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_missing_artifact_id_returns_400(self):
        resp = self.client.post(
            '/update',
            json={'patch': {'title': 'T'}}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])

    @patch('app.requests.post')
    def test_valid_payload_external_success_returns_200(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=200,
            json_data={'success': True}
        )
        resp = self.client.post('/update', json=VALID_UPDATE_PAYLOAD)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    @patch('app.requests.post')
    def test_valid_payload_external_failure_returns_502(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=500,
            ok=False,
            text='Internal Server Error'
        )
        resp = self.client.post('/update', json=VALID_UPDATE_PAYLOAD)
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(resp.get_json()['success'])

    @patch('app.requests.post')
    def test_update_calls_update_endpoint(self, mock_post):
        mock_post.return_value = _mock_response(
            status_code=200,
            json_data={'success': True}
        )
        self.client.post('/update', json=VALID_UPDATE_PAYLOAD)
        call_url = mock_post.call_args[0][0]
        self.assertIn('/v1/artifacts/update', call_url)
        self.assertNotIn('/v1/artifacts/new', call_url)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    @patch('app.requests.get')
    def test_valid_id_osc_api_returns_list(self, mock_get):
        history_list = [
            {'txId': 'tx1', 'timestamp': '2024-01-01T00:00:00Z', 'isDelete': False, 'value': {'id': 'abc-123'}}
        ]
        mock_get.return_value = _mock_response(status_code=200, json_data=history_list)
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['txId'], 'tx1')

    @patch('app.requests.get')
    def test_osc_api_non_2xx_returns_502(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404, ok=False, text='Not Found')
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])

    def test_missing_id_returns_404(self):
        resp = self.client.get('/history/')
        self.assertIn(resp.status_code, (404, 400))


class TestBuildArtifactBody(unittest.TestCase):
    def test_title_and_description_in_mandatory_public_fields(self):
        payload = {'title': 'My Title', 'description': 'My Description'}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['id'], 'art-001')
        self.assertEqual(result['mandatory_public_fields']['title'], 'My Title')
        self.assertEqual(result['mandatory_public_fields']['description'], 'My Description')

    def test_keywords_joined(self):
        payload = {'title': 'T', 'description': 'D', 'keywords': ['a', 'b', 'c']}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['keywords'], 'a,b,c')

    def test_doi_extracted(self):
        payload = {'title': 'T', 'description': 'D', 'dois': ['10.1234/test']}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['doi'], '10.1234/test')

    def test_private_fields_has_placeholder(self):
        payload = {'title': 'T', 'description': 'D'}
        result = _build_artifact_body(payload, 'art-001')
        self.assertIn('placeholder', result['private_fields'])

    def test_artifact_id_set_correctly(self):
        result = _build_artifact_body({'title': 'T', 'description': 'D'}, 'my-unique-id')
        self.assertEqual(result['id'], 'my-unique-id')


if __name__ == '__main__':
    unittest.main()
