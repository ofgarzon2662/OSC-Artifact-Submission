import hashlib
import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

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
VERIFY_TLS = True

DEFAULT_GROUP_NAME = os.getenv('ADAPTER_DEFAULT_GROUP_NAME', 'OSC.Portal').strip()
DEFAULT_API_USER_ID = os.getenv('ADAPTER_DEFAULT_API_USER_ID', 'osc.portal.admin').strip()
DEFAULT_SCHEMA_NAME = os.getenv('ADAPTER_DEFAULT_SCHEMA_NAME', 'osc.portal.dataset').strip()
ALLOW_LEGACY_DEFAULT_ROUTE = os.getenv(
    'ADAPTER_ALLOW_LEGACY_DEFAULT_ROUTE', 'true'
).strip().lower() == 'true'


def _load_organization_routes() -> Dict[str, Dict[str, str]]:
    raw = os.getenv('OSC_ORGANIZATION_ROUTES_JSON', '{}').strip() or '{}'
    try:
        routes = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError('OSC_ORGANIZATION_ROUTES_JSON is not valid JSON') from exc
    if not isinstance(routes, dict):
        raise RuntimeError('OSC_ORGANIZATION_ROUTES_JSON must contain an object')
    return routes


ORGANIZATION_ROUTES = _load_organization_routes()


def _resolve_ledger_route(
    payload: Dict[str, Any], require_schema: bool
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    organization = payload.get('organization') or {}
    organization_id = organization.get('id') if isinstance(organization, dict) else None
    configured = ORGANIZATION_ROUTES.get(organization_id, {}) if organization_id else {}

    route = {
        'groupname': organization.get('ledgerGroupName') or configured.get('groupName'),
        'apiuserid': organization.get('ledgerApiUserId') or configured.get('apiUserId'),
        'schemaname': organization.get('artifactSchemaName') or configured.get('artifactSchemaName'),
    }

    is_versioned = payload.get('contractVersion') == 'v2'
    if is_versioned and not ALLOW_LEGACY_DEFAULT_ROUTE:
        required = ['groupname', 'apiuserid']
        if require_schema:
            required.append('schemaname')
        missing = [key for key in required if not route.get(key)]
        if missing:
            return None, (
                f"Organization {organization_id or '<missing>'} has no configured "
                f"ledger route fields: {', '.join(missing)}"
            )

    route['groupname'] = route['groupname'] or DEFAULT_GROUP_NAME
    route['apiuserid'] = route['apiuserid'] or DEFAULT_API_USER_ID
    route['schemaname'] = route['schemaname'] or DEFAULT_SCHEMA_NAME
    return route, None


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


def _normalize_history_value(item: Dict[str, Any]) -> Dict[str, Any]:
    pcf = item.get('PublicCustomFields') or {}

    raw_keywords = pcf.get('keywords') or ''
    keywords = [k.strip() for k in raw_keywords.split(',') if k.strip()]

    raw_doi = pcf.get('doi') or ''
    dois = [raw_doi.strip()] if raw_doi.strip() else []

    raw_url = pcf.get('url') or ''
    links = [raw_url.strip()] if raw_url.strip() else []

    return {
        'id': item.get('ArtifactId', ''),
        'title': item.get('Title', ''),
        'description': item.get('Description', ''),
        'submissionState': 'SUCCESS',
        'submittedAt': item.get('Timestamp'),
        'submitterUsername': item.get('ContributorName') or '',
        'submitterEmail': pcf.get('contributor', ''),
        'footprint': pcf.get('footprint'),
        'manifest': pcf.get('manifest') or [],
        'acknowledgements': pcf.get('acknowledgements'),
        'fundingAgencies': pcf.get('fundingAgencies') or [],
        'keywords': keywords,
        'dois': dois,
        'links': links,
        'lastTimeVerified': None,
        'verified': False,
    }


def _build_artifact_body(payload: Dict[str, Any], artifact_id: str) -> Dict[str, Any]:
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

    private_fields: Dict[str, Any] = {}
    if not private_fields:
        # Avoid PHP json_decode(..., true) turning {} into [] by adding a placeholder.
        private_fields['placeholder'] = ''

    return {
        'id': artifact_id,
        'mandatory_public_fields': mandatory_public_fields,
        'public_fields': public_fields,
        'private_fields': private_fields
    }


def _base_api_url() -> str:
    if not API_URL:
        return ''
    if '/v1/' in API_URL:
        return API_URL[:API_URL.index('/v1/')]
    return API_URL.rstrip('/')


def _post_to_external_api(
    artifact_id: str,
    artifact_body: Dict[str, Any],
    operation: str = 'submit',
    route: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    base = _base_api_url()
    if not base:
        return { 'success': False, 'error': 'ADAPTER_API_URL is not configured' }
    if not API_TOKEN:
        return { 'success': False, 'error': 'ADAPTER_API_TOKEN is not configured' }

    if operation == 'update':
        api_url = f"{base}/v1/artifacts/update"
    else:
        api_url = f"{base}/v1/artifacts/new"

    headers = {
        'Authorization': f'Bearer {API_TOKEN}'
    }

    OSC_ARTIFACT_PREFIX = 'osc-is-artifact-'
    prefixed_id = artifact_id if artifact_id.startswith(OSC_ARTIFACT_PREFIX) else f"{OSC_ARTIFACT_PREFIX}{artifact_id}"

    ledger_route = route or {
        'groupname': DEFAULT_GROUP_NAME,
        'apiuserid': DEFAULT_API_USER_ID,
        'schemaname': DEFAULT_SCHEMA_NAME,
    }
    form_payload = {
        'groupname': ledger_route['groupname'],
        'apiuserid': ledger_route['apiuserid'],
        'schemaname': ledger_route['schemaname'],
        'artifactid': prefixed_id,
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
            if isinstance(response_payload, dict) and response_payload.get('success') is False:
                logger.error('External API reported a failed artifact operation')
                return {
                    'success': False,
                    'error': 'External API reported failure',
                    'apiStatus': response.status_code
                }
            if isinstance(response_payload, str) and 'peer command failed' in response_payload.lower():
                logger.error('External ledger artifact operation failed')
                return {
                    'success': False,
                    'error': 'External ledger operation failed',
                    'apiStatus': response.status_code
                }
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
            'error': f'External API error {response.status_code}',
            'apiStatus': response.status_code
        }
    except requests.exceptions.Timeout:
        logger.error('External API request timed out')
        return { 'success': False, 'error': 'External API request timed out' }
    except requests.exceptions.RequestException as exc:
        logger.error('External API request failed: %s', str(exc))
        return { 'success': False, 'error': 'External API request failed' }


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

    route, route_error = _resolve_ledger_route(payload, require_schema=True)
    if route_error:
        return jsonify({'success': False, 'error': route_error}), 400

    artifact_body = _build_artifact_body(data, artifact_id)
    result = _post_to_external_api(artifact_id, artifact_body, operation='submit', route=route)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


@app.post('/update')
def update() -> Any:
    payload = request.get_json(silent=True) or {}
    artifact_id = payload.get('artifactId')
    patch = payload.get('patch') or {}

    if not artifact_id:
        return jsonify({ 'success': False, 'error': 'Missing artifactId' }), 400

    route, route_error = _resolve_ledger_route(payload, require_schema=True)
    if route_error:
        return jsonify({'success': False, 'error': route_error}), 400

    artifact_body = _build_artifact_body(patch, artifact_id)
    result = _post_to_external_api(artifact_id, artifact_body, operation='update', route=route)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


@app.get('/history/<artifact_id>')
def get_history(artifact_id: str) -> Any:
    if not artifact_id or not artifact_id.strip():
        return jsonify({ 'success': False, 'error': 'Missing artifact_id' }), 400

    base = _base_api_url()
    if not base:
        return jsonify({ 'success': False, 'error': 'ADAPTER_API_URL is not configured' }), 502
    if not API_TOKEN:
        return jsonify({ 'success': False, 'error': 'ADAPTER_API_TOKEN is not configured' }), 502

    url = f"{base}/v1/artifacts/history/osc-is-artifact-{artifact_id.strip()}"
    headers = { 'Authorization': f'Bearer {API_TOKEN}' }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            verify=VERIFY_TLS
        )
        if response.ok:
            try:
                data = response.json()
            except ValueError:
                data = response.text
            if isinstance(data, list):
                normalized = []
                for i, item in enumerate(data):
                    tx_seed = f"{item.get('Timestamp', '')}-{i}"
                    tx_id = hashlib.sha256(tx_seed.encode()).hexdigest()
                    normalized.append({
                        'txId': tx_id,
                        'timestamp': item.get('Timestamp') or '',
                        'isDelete': False,
                        'value': _normalize_history_value(item)
                    })
                return jsonify(normalized), 200
            return jsonify(data), 200
        logger.error('OSC-API history error %s: %s', response.status_code, response.text)
        return jsonify({ 'success': False, 'error': f'OSC-API error {response.status_code}' }), 502
    except requests.exceptions.Timeout:
        logger.error('OSC-API history request timed out')
        return jsonify({ 'success': False, 'error': 'OSC-API history request timed out' }), 502
    except requests.exceptions.RequestException as exc:
        logger.error('OSC-API history request failed: %s', str(exc))
        return jsonify({ 'success': False, 'error': 'OSC-API history request failed' }), 502


## ─── Workflow endpoints ───────────────────────────────────────────────────────

def _build_workflow_body(payload: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {'id': workflow_id}

    if isinstance(payload.get('title'), str) and payload['title'].strip():
        body['title'] = payload['title']
    if isinstance(payload.get('description'), str) and payload['description'].strip():
        body['description'] = payload['description']
    if isinstance(payload.get('submission_comment'), str):
        body['submission_comment'] = payload['submission_comment']

    if isinstance(payload.get('keywords'), list):
        body['keywords'] = [k for k in payload['keywords'] if isinstance(k, str) and k.strip()]
    if isinstance(payload.get('artifact_ids'), list):
        body['artifact_ids'] = [a for a in payload['artifact_ids'] if isinstance(a, str) and a.strip()]
    if isinstance(payload.get('github_repositories'), list):
        body['github_repositories'] = payload['github_repositories']

    return body


def _post_workflow_to_external_api(
    workflow_id: str,
    workflow_body: Dict[str, Any],
    operation: str = 'submit',
    route: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    base = _base_api_url()
    if not base:
        return {'success': False, 'error': 'ADAPTER_API_URL is not configured'}
    if not API_TOKEN:
        return {'success': False, 'error': 'ADAPTER_API_TOKEN is not configured'}

    if operation == 'update':
        api_url = f"{base}/v1/workflows/update"
    else:
        api_url = f"{base}/v1/workflows/new"

    headers = {'Authorization': f'Bearer {API_TOKEN}'}

    OSC_WORKFLOW_PREFIX = 'osc-is-workflow-'
    prefixed_id = workflow_id if workflow_id.startswith(OSC_WORKFLOW_PREFIX) else f"{OSC_WORKFLOW_PREFIX}{workflow_id}"

    ledger_route = route or {
        'groupname': DEFAULT_GROUP_NAME,
        'apiuserid': DEFAULT_API_USER_ID,
    }
    form_payload = {
        'groupname': ledger_route['groupname'],
        'apiuserid': ledger_route['apiuserid'],
        'workflowid': prefixed_id,
        'workflowbody': json.dumps(workflow_body)
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
            if isinstance(response_payload, dict) and response_payload.get('success') is False:
                logger.error('External API reported a failed workflow operation')
                return {
                    'success': False,
                    'error': 'External API reported failure',
                    'apiStatus': response.status_code
                }
            if isinstance(response_payload, str) and 'peer command failed' in response_payload.lower():
                logger.error('External ledger workflow operation failed')
                return {
                    'success': False,
                    'error': 'External ledger operation failed',
                    'apiStatus': response.status_code
                }
            return {
                'success': True,
                'apiStatus': response.status_code,
                'apiResponse': response_payload
            }
        logger.error('External API error %s: %s', response.status_code, response.text)
        return {
            'success': False,
            'error': f'External API error {response.status_code}',
            'apiStatus': response.status_code
        }
    except requests.exceptions.Timeout:
        logger.error('External API workflow request timed out')
        return {'success': False, 'error': 'External API request timed out'}
    except requests.exceptions.RequestException as exc:
        logger.error('External API workflow request failed: %s', str(exc))
        return {'success': False, 'error': 'External API request failed'}


@app.post('/workflow/submit')
def workflow_submit() -> Any:
    payload = request.get_json(silent=True) or {}
    workflow_id = payload.get('workflowId')
    data = payload.get('data') or {}

    if not workflow_id:
        return jsonify({'success': False, 'error': 'Missing workflowId'}), 400
    if not isinstance(data.get('title'), str) or not data.get('title', '').strip():
        return jsonify({'success': False, 'error': 'Missing title'}), 400
    if not isinstance(data.get('description'), str) or not data.get('description', '').strip():
        return jsonify({'success': False, 'error': 'Missing description'}), 400

    route, route_error = _resolve_ledger_route(payload, require_schema=False)
    if route_error:
        return jsonify({'success': False, 'error': route_error}), 400

    workflow_body = _build_workflow_body(data, workflow_id)
    result = _post_workflow_to_external_api(workflow_id, workflow_body, operation='submit', route=route)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


@app.post('/workflow/update')
def workflow_update() -> Any:
    payload = request.get_json(silent=True) or {}
    workflow_id = payload.get('workflowId')
    patch = payload.get('patch') or {}

    if not workflow_id:
        return jsonify({'success': False, 'error': 'Missing workflowId'}), 400

    route, route_error = _resolve_ledger_route(payload, require_schema=False)
    if route_error:
        return jsonify({'success': False, 'error': route_error}), 400

    workflow_body = _build_workflow_body(patch, workflow_id)
    result = _post_workflow_to_external_api(workflow_id, workflow_body, operation='update', route=route)
    status_code = 200 if result.get('success') else 502
    return jsonify(result), status_code


if __name__ == '__main__':
    port = int(os.getenv('ADAPTER_PORT', '5000'))
    app.run(host='0.0.0.0', port=port)
