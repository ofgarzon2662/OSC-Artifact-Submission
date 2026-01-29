# fabric-bridge (MVP)

A TypeScript microservice that will bridge to Hyperledger Fabric via the
Fabric Gateway SDK. This MVP stubs the Fabric calls to allow the rest of the
system to integrate and evolve.

## Endpoints

- GET `/health`
- POST `/submit` with body `{ artifactId: uuid, data: object, correlationId? }`
- POST `/update` with body `{ artifactId: uuid, patch: object, correlationId? }`

## Run

```
cd fabric-bridge
npm i
npm run dev
```

Environment (defaults in code):
- PORT (default 4000)
- FABRIC_CHANNEL, FABRIC_CHAINCODE
- FABRIC_PEER (e.g. localhost:17051)
- WALLET_PATH (filesystem wallet path)
- MSP_ID, IDENTITY_LABEL

## Build

```
npm run build
npm start
```

## Next steps
- Replace stub with real Fabric Gateway SDK connection (`FileSystemWallet`, `Gateway`, `Network/Contract`)
- Add TLS connection profile and proper discovery
- Add metrics and structured error mapping
