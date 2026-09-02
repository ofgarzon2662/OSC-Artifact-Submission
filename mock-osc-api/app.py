# Mock OSC-API — mimics the Hyperledger Fabric PHP bridge for local dev/testing
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="mock-osc-api")


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_tx_response() -> dict:
    return {
        "txId": f"mock-tx-{uuid.uuid4()}",
        "peerId": "peer0.org1.example.com",
        "timestamp": _now_rfc3339(),
    }


@app.get("/health")
async def health() -> Any:
    return {"status": "ok", "service": "mock-osc-api"}


@app.post("/v1/artifacts/new")
async def new_artifact(request: Request) -> Any:
    body = None
    try:
        body = await request.json()
    except Exception:
        body = await request.body()
        body = body.decode("utf-8", errors="replace") if body else None
    logger.info("POST /v1/artifacts/new — body: %s", body)
    return JSONResponse(content=_mock_tx_response(), status_code=200)


@app.post("/v1/artifacts/update")
async def update_artifact(request: Request) -> Any:
    body = None
    try:
        body = await request.json()
    except Exception:
        body = await request.body()
        body = body.decode("utf-8", errors="replace") if body else None
    logger.info("POST /v1/artifacts/update — body: %s", body)
    return JSONResponse(content=_mock_tx_response(), status_code=200)


@app.get("/v1/artifacts/history/{artifact_id}")
async def get_history(artifact_id: str, request: Request) -> Any:
    logger.info("GET /v1/artifacts/history/%s", artifact_id)
    now = datetime.now(timezone.utc)

    def _entry(offset_seconds: int) -> dict:
        ts = (now - timedelta(seconds=offset_seconds)).isoformat()
        return {
            "ArtifactId": artifact_id,
            "Timestamp": ts,
            "ContributorName": "Mock Contributor",
            "EditorName": "Mock Editor",
            "GroupName": "Mock Group",
            "Title": "Mock Artifact Title",
            "Description": "Mock artifact description for local testing",
            "SubmissionComment": "Initial submission",
            "Keywords": ["test", "mock"],
            "Links": [],
            "Dois": [],
            "FundingAgencies": [],
            "Acknowledgements": "",
            "Footprint": "a" * 64,
            "Manifest": [
                {
                    "hash": "b" * 64,
                    "filename": "test.txt",
                    "algorithm": "SHA-256",
                }
            ],
            "PublicCustomFields": {},
            "SubmissionState": "SUCCESS",
            "BlockchainTxId": f"mock-tx-{uuid.uuid4()}",
            "isDelete": False,
        }

    history = [_entry(0), _entry(60), _entry(120)]
    return JSONResponse(content=history, status_code=200)
