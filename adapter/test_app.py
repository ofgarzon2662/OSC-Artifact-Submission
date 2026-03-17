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
    def test_valid_id_osc_api_returns_normalized_list(self, mock_get):
        # Adapter now normalizes blockchain items: generates txId, maps fields
        history_list = [
            {'ArtifactId': 'osc-is-artifact-abc-123', 'Title': 'T', 'Description': 'D',
             'Timestamp': '2024-01-01T00:00:00Z', 'PublicCustomFields': {}}
        ]
        mock_get.return_value = _mock_response(status_code=200, json_data=history_list)
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        item = data[0]
        self.assertIn('txId', item)
        self.assertEqual(len(item['txId']), 64)  # SHA-256 hex digest
        self.assertEqual(item['timestamp'], '2024-01-01T00:00:00Z')
        self.assertFalse(item['isDelete'])
        self.assertEqual(item['value']['title'], 'T')

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


class TestFirstString(unittest.TestCase):
    """Tests for the _first_string helper."""

    def test_returns_first_non_empty_string_from_list(self):
        from app import _first_string
        self.assertEqual(_first_string(['', '  ', 'hello']), 'hello')

    def test_returns_none_for_empty_list(self):
        from app import _first_string
        self.assertIsNone(_first_string([]))

    def test_returns_stripped_string_value(self):
        from app import _first_string
        self.assertEqual(_first_string('  world  '), 'world')

    def test_returns_none_for_blank_string(self):
        from app import _first_string
        self.assertIsNone(_first_string('   '))

    def test_returns_none_for_non_string_non_list(self):
        from app import _first_string
        self.assertIsNone(_first_string(None))


class TestJoinStrings(unittest.TestCase):
    """Tests for the _join_strings helper."""

    def test_joins_list_with_commas(self):
        from app import _join_strings
        self.assertEqual(_join_strings(['a', 'b', 'c']), 'a,b,c')

    def test_returns_none_for_empty_list(self):
        from app import _join_strings
        self.assertIsNone(_join_strings([]))

    def test_returns_none_for_list_of_blank_strings(self):
        from app import _join_strings
        self.assertIsNone(_join_strings(['  ', '  ']))

    def test_returns_stripped_string_value(self):
        from app import _join_strings
        self.assertEqual(_join_strings('  hello  '), 'hello')

    def test_returns_none_for_blank_string(self):
        from app import _join_strings
        self.assertIsNone(_join_strings(''))


class TestBaseApiUrl(unittest.TestCase):
    """Tests for the _base_api_url helper."""

    def test_strips_v1_path_and_returns_base(self):
        from app import _base_api_url
        import app as adapter_app
        original = adapter_app.API_URL
        adapter_app.API_URL = 'https://host/v1/artifacts'
        try:
            result = _base_api_url()
            self.assertEqual(result, 'https://host')
        finally:
            adapter_app.API_URL = original

    def test_returns_empty_string_when_api_url_unset(self):
        from app import _base_api_url
        import app as adapter_app
        original = adapter_app.API_URL
        adapter_app.API_URL = ''
        try:
            result = _base_api_url()
            self.assertEqual(result, '')
        finally:
            adapter_app.API_URL = original

    def test_strips_trailing_slash_when_no_v1(self):
        from app import _base_api_url
        import app as adapter_app
        original = adapter_app.API_URL
        adapter_app.API_URL = 'https://host/'
        try:
            result = _base_api_url()
            self.assertEqual(result, 'https://host')
        finally:
            adapter_app.API_URL = original


class TestPostToExternalApiUnconfigured(unittest.TestCase):
    """Tests for _post_to_external_api when env vars are not configured."""

    def test_returns_failure_when_api_url_empty(self):
        from app import _post_to_external_api
        import app as adapter_app
        original_url = adapter_app.API_URL
        adapter_app.API_URL = ''
        try:
            result = _post_to_external_api('art-1', {})
            self.assertFalse(result['success'])
            self.assertIn('ADAPTER_API_URL', result['error'])
        finally:
            adapter_app.API_URL = original_url

    def test_returns_failure_when_api_token_empty(self):
        from app import _post_to_external_api
        import app as adapter_app
        original_url = adapter_app.API_URL
        original_token = adapter_app.API_TOKEN
        adapter_app.API_URL = 'https://host/v1/artifacts'
        adapter_app.API_TOKEN = ''
        try:
            result = _post_to_external_api('art-1', {})
            self.assertFalse(result['success'])
            self.assertIn('ADAPTER_API_TOKEN', result['error'])
        finally:
            adapter_app.API_URL = original_url
            adapter_app.API_TOKEN = original_token

    @patch('app.requests.post')
    def test_returns_failure_when_response_text_contains_peer_command_failed(self, mock_post):
        from app import _post_to_external_api
        import app as adapter_app
        original_url = adapter_app.API_URL
        original_token = adapter_app.API_TOKEN
        adapter_app.API_URL = 'https://host/v1/artifacts'
        adapter_app.API_TOKEN = 'tok'
        mock_post.return_value = _mock_response(
            status_code=200,
            text='peer command failed: endorsement failed',
        )
        # Override json to return string (text response)
        mock_post.return_value.json.side_effect = ValueError('no json')
        mock_post.return_value.text = 'peer command failed: endorsement failed'
        try:
            result = _post_to_external_api('art-1', {'id': 'art-1', 'mandatory_public_fields': {}, 'public_fields': {}, 'private_fields': {}})
            self.assertFalse(result['success'])
        finally:
            adapter_app.API_URL = original_url
            adapter_app.API_TOKEN = original_token

    @patch('app.requests.post')
    def test_returns_failure_on_request_exception(self, mock_post):
        from app import _post_to_external_api
        import app as adapter_app
        import requests as req_lib
        original_url = adapter_app.API_URL
        original_token = adapter_app.API_TOKEN
        adapter_app.API_URL = 'https://host/v1/artifacts'
        adapter_app.API_TOKEN = 'tok'
        mock_post.side_effect = req_lib.exceptions.RequestException('connection refused')
        try:
            result = _post_to_external_api('art-1', {})
            self.assertFalse(result['success'])
            self.assertIn('connection refused', result['error'])
        finally:
            adapter_app.API_URL = original_url
            adapter_app.API_TOKEN = original_token


class TestHistoryEndpointAdditional(unittest.TestCase):
    """Additional tests for /history/<artifact_id> endpoint."""

    def setUp(self):
        self.client = app.test_client()

    @patch('app.requests.get')
    def test_history_timeout_returns_502(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.Timeout()
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('timed out', data['error'])

    @patch('app.requests.get')
    def test_history_request_exception_returns_502(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.RequestException('connection refused')
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn('connection refused', data['error'])

    @patch('app.requests.get')
    def test_history_non_json_response_returns_text(self, mock_get):
        mock_get.return_value = _mock_response(status_code=200, text='plain text body', json_data=None)
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 200)

    def test_history_api_url_not_configured_returns_502(self):
        import app as adapter_app
        original = adapter_app.API_URL
        adapter_app.API_URL = ''
        try:
            resp = self.client.get('/history/abc-123')
            self.assertEqual(resp.status_code, 502)
            self.assertFalse(resp.get_json()['success'])
        finally:
            adapter_app.API_URL = original

    def test_history_api_token_not_configured_returns_502(self):
        import app as adapter_app
        original_url = adapter_app.API_URL
        original_token = adapter_app.API_TOKEN
        adapter_app.API_URL = 'https://host/v1/artifacts'
        adapter_app.API_TOKEN = ''
        try:
            resp = self.client.get('/history/abc-123')
            self.assertEqual(resp.status_code, 502)
            self.assertFalse(resp.get_json()['success'])
        finally:
            adapter_app.API_URL = original_url
            adapter_app.API_TOKEN = original_token


class TestBuildArtifactBodyAdditional(unittest.TestCase):
    """Additional tests for _build_artifact_body helper."""

    def test_contributor_extracted(self):
        payload = {'title': 'T', 'description': 'D', 'contributor': 'user@example.com'}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['contributor'], 'user@example.com')

    def test_footprint_extracted(self):
        fp = 'a' * 64
        payload = {'title': 'T', 'description': 'D', 'footprint': fp}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['footprint'], fp)

    def test_manifest_extracted(self):
        manifest = [{'hash': 'a' * 64, 'filename': 'f.txt', 'algorithm': 'sha256'}]
        payload = {'title': 'T', 'description': 'D', 'manifest': manifest}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['manifest'], manifest)

    def test_acknowledgements_extracted(self):
        payload = {'title': 'T', 'description': 'D', 'acknowledgements': 'Thanks'}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['acknowledgements'], 'Thanks')

    def test_funding_agencies_extracted(self):
        payload = {'title': 'T', 'description': 'D', 'fundingAgencies': ['NSF', 'NIH']}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['fundingAgencies'], ['NSF', 'NIH'])

    def test_url_extracted_from_links(self):
        payload = {'title': 'T', 'description': 'D', 'links': ['https://example.com']}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['public_fields']['url'], 'https://example.com')

    def test_submission_comment_extracted(self):
        payload = {'title': 'T', 'description': 'D', 'submission_comment': 'My comment'}
        result = _build_artifact_body(payload, 'art-001')
        self.assertEqual(result['mandatory_public_fields']['submission_comment'], 'My comment')

    def test_empty_payload_produces_minimal_body(self):
        result = _build_artifact_body({}, 'art-001')
        self.assertEqual(result['id'], 'art-001')
        self.assertEqual(result['mandatory_public_fields'], {})
        self.assertIn('placeholder', result['private_fields'])


class TestUpdateEndpointAdditional(unittest.TestCase):
    """Additional tests for /update endpoint."""

    def setUp(self):
        self.client = app.test_client()

    @patch('app.requests.post')
    def test_update_timeout_returns_502(self, mock_post):
        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.Timeout()
        resp = self.client.post('/update', json={'artifactId': 'abc-123', 'patch': {'title': 'T'}})
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(resp.get_json()['success'])

    @patch('app.requests.post')
    def test_update_request_exception_returns_502(self, mock_post):
        import requests as req_lib
        mock_post.side_effect = req_lib.exceptions.RequestException('network error')
        resp = self.client.post('/update', json={'artifactId': 'abc-123', 'patch': {}})
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertFalse(data['success'])

    @patch('app.requests.post')
    def test_update_artifact_id_prefixed_correctly(self, mock_post):
        mock_post.return_value = _mock_response(status_code=200, json_data={'success': True})
        self.client.post('/update', json={'artifactId': 'abc-123', 'patch': {}})
        call_kwargs = mock_post.call_args[1]
        self.assertIn('osc-is-artifact-abc-123', call_kwargs['data']['artifactid'])

    @patch('app.requests.post')
    def test_already_prefixed_artifact_id_not_double_prefixed(self, mock_post):
        mock_post.return_value = _mock_response(status_code=200, json_data={'success': True})
        self.client.post('/update', json={'artifactId': 'osc-is-artifact-abc-123', 'patch': {}})
        call_kwargs = mock_post.call_args[1]
        # Should NOT double-prefix
        self.assertNotIn('osc-is-artifact-osc-is-artifact', call_kwargs['data']['artifactid'])


class TestNormalizeHistoryValue(unittest.TestCase):
    """Tests for the _normalize_history_value helper."""

    BLOCKCHAIN_ITEM = {
        'ArtifactId': 'osc-is-artifact-abc-123',
        'Title': 'My Title',
        'Description': 'My Description',
        'Timestamp': '2026-03-17T12:00:00Z',
        'ContributorName': 'juanpablo',
        'ContributorUUID': 'uuid-1',
        'PublicCustomFields': {
            'footprint': 'deadbeef' * 8,
            'keywords': 'ai, ml, data',
            'doi': '10.1234/test',
            'url': 'https://example.com',
            'contributor': 'juanpablo@example.edu',
            'acknowledgements': 'Thanks everyone',
            'fundingAgencies': ['NSF', 'NIH'],
            'manifest': [
                {'algorithm': 'sha256', 'filename': 'f.txt', 'hash': 'abc123'}
            ],
        },
    }

    def _normalize(self, item=None):
        from app import _normalize_history_value
        return _normalize_history_value(item or self.BLOCKCHAIN_ITEM)

    def test_title_and_description_extracted(self):
        v = self._normalize()
        self.assertEqual(v['title'], 'My Title')
        self.assertEqual(v['description'], 'My Description')

    def test_id_extracted(self):
        v = self._normalize()
        self.assertEqual(v['id'], 'osc-is-artifact-abc-123')

    def test_submission_state_is_success(self):
        v = self._normalize()
        self.assertEqual(v['submissionState'], 'SUCCESS')

    def test_submitter_email_from_public_fields_contributor(self):
        v = self._normalize()
        self.assertEqual(v['submitterEmail'], 'juanpablo@example.edu')

    def test_submitter_username_from_contributor_name(self):
        v = self._normalize()
        self.assertEqual(v['submitterUsername'], 'juanpablo')

    def test_empty_contributor_name_gives_empty_string(self):
        item = {**self.BLOCKCHAIN_ITEM, 'ContributorName': ''}
        v = self._normalize(item)
        self.assertEqual(v['submitterUsername'], '')

    def test_footprint_extracted(self):
        v = self._normalize()
        self.assertEqual(v['footprint'], 'deadbeef' * 8)

    def test_keywords_split_into_list(self):
        v = self._normalize()
        self.assertEqual(v['keywords'], ['ai', 'ml', 'data'])

    def test_keywords_empty_string_gives_empty_list(self):
        item = {**self.BLOCKCHAIN_ITEM, 'PublicCustomFields': {**self.BLOCKCHAIN_ITEM['PublicCustomFields'], 'keywords': ''}}
        v = self._normalize(item)
        self.assertEqual(v['keywords'], [])

    def test_doi_wrapped_in_list(self):
        v = self._normalize()
        self.assertEqual(v['dois'], ['10.1234/test'])

    def test_empty_doi_gives_empty_list(self):
        item = {**self.BLOCKCHAIN_ITEM, 'PublicCustomFields': {**self.BLOCKCHAIN_ITEM['PublicCustomFields'], 'doi': ''}}
        v = self._normalize(item)
        self.assertEqual(v['dois'], [])

    def test_url_wrapped_in_list(self):
        v = self._normalize()
        self.assertEqual(v['links'], ['https://example.com'])

    def test_funding_agencies_passthrough(self):
        v = self._normalize()
        self.assertEqual(v['fundingAgencies'], ['NSF', 'NIH'])

    def test_manifest_passthrough(self):
        v = self._normalize()
        self.assertEqual(len(v['manifest']), 1)
        self.assertEqual(v['manifest'][0]['filename'], 'f.txt')

    def test_acknowledgements_extracted(self):
        v = self._normalize()
        self.assertEqual(v['acknowledgements'], 'Thanks everyone')

    def test_missing_public_custom_fields_returns_safe_defaults(self):
        item = {'ArtifactId': 'x', 'Title': 'T', 'Description': 'D', 'Timestamp': '2026-01-01Z'}
        v = self._normalize(item)
        self.assertIsNone(v['footprint'])
        self.assertEqual(v['keywords'], [])
        self.assertEqual(v['dois'], [])
        self.assertEqual(v['links'], [])
        self.assertEqual(v['fundingAgencies'], [])
        self.assertEqual(v['manifest'], [])


class TestHistoryNormalizationEndToEnd(unittest.TestCase):
    """Tests that /history/<id> normalizes blockchain items before returning."""

    def setUp(self):
        self.client = app.test_client()

    @patch('app.requests.get')
    def test_blockchain_list_normalized_to_ghw_format(self, mock_get):
        raw_item = {
            'ArtifactId': 'osc-is-artifact-abc-123',
            'Title': 'My Title',
            'Description': 'My Description',
            'Timestamp': '2026-03-17T12:00:00Z',
            'ContributorName': '',
            'PublicCustomFields': {
                'footprint': 'abc',
                'keywords': 'ai, ml',
                'doi': '10.1/x',
                'url': 'https://example.com',
                'contributor': 'user@example.com',
                'fundingAgencies': ['NSF'],
                'manifest': [],
            },
        }
        mock_get.return_value = _mock_response(status_code=200, json_data=[raw_item])
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        item = data[0]
        self.assertIn('txId', item)
        self.assertIn('timestamp', item)
        self.assertEqual(item['isDelete'], False)
        self.assertEqual(item['timestamp'], '2026-03-17T12:00:00Z')
        v = item['value']
        self.assertEqual(v['title'], 'My Title')
        self.assertEqual(v['submissionState'], 'SUCCESS')
        self.assertEqual(v['submitterEmail'], 'user@example.com')
        self.assertEqual(v['keywords'], ['ai', 'ml'])
        self.assertEqual(v['dois'], ['10.1/x'])
        self.assertEqual(v['links'], ['https://example.com'])

    @patch('app.requests.get')
    def test_deterministic_tx_id_for_same_timestamp_and_index(self, mock_get):
        raw_item = {'ArtifactId': 'x', 'Title': 'T', 'Description': 'D', 'Timestamp': '2026-01-01T00:00:00Z'}
        mock_get.return_value = _mock_response(status_code=200, json_data=[raw_item])
        resp1 = self.client.get('/history/abc-123')
        mock_get.return_value = _mock_response(status_code=200, json_data=[raw_item])
        resp2 = self.client.get('/history/abc-123')
        self.assertEqual(resp1.get_json()[0]['txId'], resp2.get_json()[0]['txId'])

    @patch('app.requests.get')
    def test_non_list_response_passed_through_unchanged(self, mock_get):
        mock_get.return_value = _mock_response(status_code=200, json_data={'error': 'unexpected'})
        resp = self.client.get('/history/abc-123')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), dict)


if __name__ == '__main__':
    unittest.main()
