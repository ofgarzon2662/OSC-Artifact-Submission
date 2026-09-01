# OSC Artifact Submission Architecture

This repository owns the asynchronous path between the OSC API Gateway and the
Hyperledger Fabric provenance contract. The current path intentionally excludes
the legacy OSC-API, mock-osc-api, and compatibility adapter from Kubernetes and
AWS deployments.

## Runtime responsibilities

| Component | Responsibility |
|---|---|
| Submission worker | Validate v3 command envelopes, select the organization Ledger Gateway, drive the ledger operation, and publish a confirmed completion event. |
| Ledger Gateway | Hold one mounted organization service identity, enforce organization-bound input, and call the namespaced provenance contract over Fabric Gateway gRPC TLS. |
| Submission listener | Validate completion events and reconcile artifact/workflow status with the API Gateway. |
| RabbitMQ | Persist commands and completions, delay bounded retries, and park unprocessable completion events. |

Two Ledger Gateway deployments are required:

| Organization | MSP | Worker configuration |
|---|---|---|
| nEUROSCIENCE GATEWAY | `NSGMSP` | `LEDGER_GATEWAY_NSG_URL`, `LEDGER_GATEWAY_NSG_TOKEN` |
| CITIZEN SCIENCE | `CitizenScienceMSP` | `LEDGER_GATEWAY_CITIZEN_SCIENCE_URL`, `LEDGER_GATEWAY_CITIZEN_SCIENCE_TOKEN` |

The worker does not trust an arbitrary URL from the command. It selects from
this fixed routing table, and the destination Ledger Gateway independently
rejects a mismatched organization or MSP.

## Identity model

The API Gateway authenticates portal users, revalidates their active
organization membership, and authorizes organization-scoped roles. Fabric uses
a narrowly scoped service identity per organization. Every chaincode request
contains:

- authenticated application user ID;
- active organization ID;
- correlation ID;
- exact operation;
- request timestamp.

This demonstrates organization-level cryptographic submission and individual
attribution through the trusted application audit path. It does not claim that
each portal user owns a distinct Fabric certificate.

## Failure behavior

| Failure | Result |
|---|---|
| Invalid v3 envelope or cross-organization route | Rejected before the Ledger Gateway call; terminal failure completion. |
| Temporary Ledger Gateway or Fabric outage | Delayed dead-letter retry; no premature failure event. |
| Retry limit reached | Terminal failure completion, then command acknowledgement. |
| Temporary API Gateway outage during reconciliation | Delayed completion-event retry. |
| Invalid or exhausted completion event | Persisted in `osc.status.failed.queue`. |
| Worker restart after a Fabric commit | Redelivery uses the same correlation ID; chaincode returns the prior receipt without a duplicate state transition. |

## Security controls

- AMQPS is mandatory for AWS, staging, and production modes.
- Fabric gRPC is TLS-only and verifies the peer server name.
- Internal HTTP calls use separate secret tokens.
- Certificates, keys, and tokens are mounted or injected at runtime and excluded
  from images, health responses, logs, and repository files.
- Containers run as numeric non-root user `10001`.
- Dependency installation uses hash-locked Python packages or the repository
  npm supply-chain wrapper.

## Experimental boundaries

The local Kind and ephemeral EKS environments validate installation,
organization isolation, provenance, retries, failure recovery, rollout, and
rollback. They are engineering experiments, not evidence of researcher adoption
or production readiness. The inexpensive AWS topology intentionally does not
operate every production high-availability alternative.
