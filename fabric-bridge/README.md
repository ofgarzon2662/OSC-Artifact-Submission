# OSC Ledger Gateway

The directory name is retained for repository compatibility, but this service is
now a dedicated **Ledger Gateway**. One deployment is bound to one OSC
organization and one Fabric service identity. It is not the legacy OSC-API
compatibility adapter.

## Trust boundary

- Supported identities are `NSGMSP` / `nsg` and
  `CitizenScienceMSP` / `citizen-science`.
- A deployment accepts commands only for its configured organization.
- Calls require `Authorization: Bearer <LEDGER_GATEWAY_TOKEN>`.
- Fabric uses the Gateway API over gRPC TLS. Insecure gRPC is not available.
- Certificates and private keys are mounted files. They are never downloaded by
  the image and never returned by `/health`.
- Portal authentication and roles remain in the API Gateway. Chaincode receives
  authenticated user, active organization, operation, request time, and
  correlation metadata for audit attribution.

## API

All command bodies use `contractVersion: "v3"`, an `organization` object, a
top-level `correlationId`, and matching `request` metadata.

| Endpoint | Chaincode transaction |
|---|---|
| `POST /submit` | `ProvenanceContract:CreateArtifact` |
| `POST /update` | `ProvenanceContract:UpdateArtifact` |
| `POST /workflow/submit` | `ProvenanceContract:CreateWorkflow` |
| `POST /workflow/update` | `ProvenanceContract:UpdateWorkflow` |
| `GET /history/:id` | `ProvenanceContract:GetArtifactHistory` |
| `GET /workflow/history/:id` | `ProvenanceContract:GetWorkflowHistory` |
| `GET /health` | Sanitized liveness/configuration status |

## Required configuration

```text
MSP_ID
ORGANIZATION_ID
LEDGER_GATEWAY_TOKEN
PEER_ENDPOINT
TLS_SERVER_NAME
TLS_CERT_PATH
CERTIFICATE_PATH
PRIVATE_KEY_PATH
FABRIC_CHANNEL
FABRIC_CHAINCODE
```

`FABRIC_REAL_MODE=false` is for deterministic local contract tests only. The
container defaults to real Fabric mode and fails startup when its identity or
TLS configuration is incomplete.

## Development

Use the repository secure-install wrapper rather than a direct npm install.

```powershell
.\scripts\secure-install.ps1
npm run build
npm test
```
