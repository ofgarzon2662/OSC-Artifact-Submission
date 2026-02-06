import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

if os.path.exists('.env'):
    load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

API_URL = os.getenv('ADAPTER_API_URL', '').strip()
API_TOKEN = os.getenv('ADAPTER_API_TOKEN', '').strip()
REQUEST_TIMEOUT_SECONDS = float(os.getenv('ADAPTER_REQUEST_TIMEOUT_SECONDS', '30'))
VERIFY_TLS = os.getenv('ADAPTER_VERIFY_TLS', 'true').strip().lower() not in ('0', 'false', 'no')

# Hard-coded per request
GROUPNAME = 'OSC.Portal'
APIUSERID = 'osc.portal.admin'
SCHEMANAME = 'osc.portal.dataset'


def _first_string(value: Any) -> Optional[str]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _join_strings(value: Any) -> Optional[str]:
    if isinstance(value, list):
        cleaned = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        if cleaned:
            return ','.join(cleaned)
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _build_artifact_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    mandatory_public_fields: Dict[str, Any] = {}
    public_fields: Dict[str, Any] = {}

    title = payload.get('title')
    description = payload.get('description')
    submission_comment = payload.get('submission_comment')

    if isinstance(title, str) and title.strip():
        mandatory_public_fields['title'] = title
    if isinstance(description, str) and description.strip():
        mandatory_public_fields['description'] = description
    if isinstance(submission_comment, str):
        mandatory_public_fields['submission_comment'] = submission_comment

    keywords = _join_strings(payload.get('keywords'))
    if keywords is not None:
        public_fields['keywords'] = keywords

    doi = _first_string(payload.get('dois') or payload.get('doi'))
    if doi is not None:
        public_fields['doi'] = doi

    url = _first_string(payload.get('links') or payload.get('url'))
    if url is not None:
        public_fields['url'] = url

    contributor = _first_string(payload.get('contributor'))
    if contributor is not None:
        public_fields['contributor'] = contributor

    footprint = payload.get('footprint')
    if isinstance(footprint, str) and footprint.strip():
        public_fields['footprint'] = footprint

    manifest = payload.get('manifest')
    if isinstance(manifest, list):
        public_fields['manifest'] = manifest

    acknowledgements = payload.get('acknowledgements')
    if isinstance(acknowledgements, str):
        public_fields['acknowledgements'] = acknowledgements

    funding_agencies = payload.get('fundingAgencies')
    if funding_agencies is not None:
        public_fields['fundingAgencies'] = funding_agencies

    return {
        'mandatory_public_fields': mandatory_public_fields,
        'public_fields': public_fields,
        'private_fields': {}
    }


def _resolve_api_url() -> str:
    if not API_URL:
        return ''
    if '/v1/' in API_URL:
        return API_URL
    return f"{API_URL.rstrip('/')}/v1/artifacts/new"


def _post_to_external_api(artifact_id: str, artifact_body: Dict[str, Any]) -> Dict[str, Any]:
    api_url = _resolve_api_url()
    if not api_url:
        return { 'success': False, 'error': 'ADAPTER_API_URL is not configured' }
    if not API_TOKEN:
        return { 'success': False, 'error': 'ADAPTER_API_TOKEN is not configured' }

    headers = {
        'Authorization': f'Bearer {API_TOKEN}'
    }

    form_payload = {
        'groupname': GROUPNAME,
        'apiuserid': APIUSERID,
        'schemaname': SCHEMANAME,
        'artifactid': artifact_id,
        'artifactbody': json.dumps(artifact_body)
    }

    try:
        response = requests.post(
            api_url,
            data=form_payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            verify=VERIFY_TLS
        )
        if response.ok:
            try:
                response_payload = response.json()
            except ValueError:
                response_payload = response.text
            return {
                'success': True,
                'apiStatus': response.status_code,
                'apiResponse': response_payload
            }
        logger.error(
            'External API error %s: %s',
            response.status_code,
            response.text
        )
        return {
            'success': False,
            'error': f'External API error {response.status_code}: {response.text}',
            'apiStatus': response.status_code,
            'apiResponse': response.text
        }
    except requests.exceptions.Timeout:
        logger.error('External API request timed out')
        return { 'success': False, 'error': 'External API request timed out' }
    except requests.exceptions.RequestException as exc:
        logger.error('External API request failed: %s', str(exc))
        return { 'success': False, 'error': f'External API request failed: {str(exc)}' }


@app.get('/health')
def health() -> Any:
    return jsonify({
        'status': 'healthy',
        'service': 'adapter',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@app.post('/submit')
def submit() -> Any:
    payload = request.get_json(silent=True) or {}
    artifact_id = payload.get('artifactId')
    data = payload.get('data') or {}

    if not artifact_id:
        return jsonify({ 'success': False, 'error': 'Missing artifactId' }), 400
    if not isinstance(data.get('title'), str) or not data.get('title', '').strip():
        return jsonify({ 'success': False, 'error': 'Missing title' }), 400
    if not isinstance(data.get('description'), str) or not data.get('description', '').strip():
        return jsonify({ 'success': False, 'error': 'Missing description' }), 400

    artifact_body = _build_artifact_body(data)
    result = _post_to_external_api(artifact_id, artifact_body)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


@app.post('/update')
def update() -> Any:
    payload = request.get_json(silent=True) or {}
    artifact_id = payload.get('artifactId')
    patch = payload.get('patch') or {}

    if not artifact_id:
        return jsonify({ 'success': False, 'error': 'Missing artifactId' }), 400

    artifact_body = _build_artifact_body(patch)
    result = _post_to_external_api(artifact_id, artifact_body)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


if __name__ == '__main__':
    port = int(os.getenv('ADAPTER_PORT', '5000'))
    app.run(host='0.0.0.0', port=port)
