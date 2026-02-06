# Adapter service

Small HTTP adapter used by the submission worker. It accepts the same `/submit` and `/update`
payloads as the fabric-bridge and forwards them to the external API.

## Environment

- `ADAPTER_API_URL` (required): External API endpoint URL
- `ADAPTER_API_TOKEN` (required): Bearer token for the external API
- `ADAPTER_PORT` (optional): Port to listen on (default `5000`)
- `ADAPTER_REQUEST_TIMEOUT_SECONDS` (optional): Request timeout in seconds (default `30`)
- `ADAPTER_VERIFY_TLS` (optional): Verify TLS certs (default `true`)

Hard-coded form fields:
- `groupname`: `OSC.Portal`
- `apiuserid`: `osc.portal.admin`
- `schemaname`: `osc.portal.dataset`
