import asyncio
import secrets
import time
import random
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Mock Blockchain Peer",
    description="Simulates blockchain peer for artifact submission with test automation support",
    version="1.0.0"
)

# Request/Response models
class ArtifactSubmissionRequest(BaseModel):
    artifactId: str
    data: Dict[str, Any]
    timestamp: str

class ArtifactSubmissionResponse(BaseModel):
    success: bool
    txId: str = None
    peerId: str = None
    error: str = None
    timestamp: str
    processing_time: float

class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str
    uptime: float

# Service startup time for uptime calculation
startup_time = time.time()

# Simulated peer ID (in real blockchain, this would be the actual peer ID)
PEER_ID = "12D3KooWBhxQ7uXeY9zF8qG5nM4rL3pT6vN8wS2cK9jH1fX7yR4e"

def determine_success_from_artifact(artifact_data: Dict[str, Any]) -> tuple[bool, str]:
    """
    Determine submission success based on artifact title patterns.
    This enables deterministic testing without container restarts.

    Test Patterns:
    - Title contains a failure pattern (e.g., 'test_gas', 'force_fail') -> Always FAILS with a specific error.
    - No failure pattern in title -> Always SUCCEEDS.
    """
    title = artifact_data.get('title', '').lower()
    logger.info(f"🔍 Analyzing artifact title for test patterns: \"{title}\"")

    failure_patterns = {
        'test_gas': 'Insufficient gas fees',
        'force_gas': 'Insufficient gas fees',
        'test_network': 'Network congestion - transaction rejected',
        'force_network': 'Network congestion - transaction rejected',
        'test_timeout': 'Peer connection timeout',
        'force_timeout': 'Peer connection timeout',
        'test_invalid': 'Invalid artifact data format',
        'force_invalid': 'Invalid artifact data format',
        'test_fail': 'Blockchain temporarily unavailable',
        'force_fail': 'Blockchain temporarily unavailable',
        'test_rejected': 'Transaction rejected by blockchain',
        'force_rejected': 'Transaction rejected by blockchain'
    }

    for pattern, error_msg in failure_patterns.items():
        if pattern in title:
            logger.info(f"❌ FAILURE pattern found: '{pattern}' - Forcing failure: {error_msg}")
            return False, error_msg

    # If no failure patterns are found, default to success.
    logger.info("✅ No failure patterns found in title. Forcing success.")
    return True, None

@app.post("/submit-artifact", response_model=ArtifactSubmissionResponse)
async def submit_artifact(request: ArtifactSubmissionRequest):
    """
    Submit an artifact to the blockchain (simulated).
    
    Supports test automation through artifact data patterns:
    - Title/Description with 'test_success' → Always succeeds
    - Title/Description with 'test_gas' → Always fails with gas error
    - Keywords with 'test-success' → Always succeeds
    - No patterns → Deterministic random (95% success)
    """
    start_time = time.time()
    
    logger.info(f"📨 Received artifact submission: {request.artifactId}")
    
    try:
        # Simulate blockchain processing delay
        await asyncio.sleep(0.3)  # 300ms delay
        
        # Determine result based on artifact data patterns
        success, error_msg = determine_success_from_artifact(request.data)
        processing_time = time.time() - start_time
        
        if success:
            # Generate deterministic transaction ID (same artifact = same txId)
            tx_seed = f"{request.artifactId}-{PEER_ID}"
            tx_hash = hashlib.sha256(tx_seed.encode()).hexdigest()
            tx_id = f"0x{tx_hash[:64]}"  # 64-char hex string
            
            logger.info(f"✅ SUCCESS: Artifact {request.artifactId} submitted with txId: {tx_id}")
            
            return ArtifactSubmissionResponse(
                success=True,
                txId=tx_id,
                peerId=PEER_ID,
                timestamp=datetime.now(timezone.utc).isoformat(),
                processing_time=processing_time
            )
        else:
            logger.warning(f"❌ FAILURE: Artifact {request.artifactId} failed: {error_msg}")
            
            return ArtifactSubmissionResponse(
                success=False,
                error=error_msg,
                peerId=PEER_ID,
                timestamp=datetime.now(timezone.utc).isoformat(),
                processing_time=processing_time
            )
            
    except Exception as e:
        processing_time = time.time() - start_time
        error_msg = f"Unexpected error during submission: {str(e)}"
        
        logger.error(f"💥 ERROR: Processing artifact {request.artifactId}: {error_msg}")
        
        return ArtifactSubmissionResponse(
            success=False,
            error=error_msg,
            timestamp=datetime.now(timezone.utc).isoformat(),
            processing_time=processing_time
        )

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for monitoring."""
    uptime = time.time() - startup_time
    
    return HealthResponse(
        status="healthy",
        service="mock-blockchain-peer",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime=uptime
    )

@app.get("/")
async def root():
    """Root endpoint with service information and test patterns."""
    return {
        "service": "Mock Blockchain Peer",
        "version": "1.0.0",
        "description": "Simulates blockchain peer with test automation support",
        "endpoints": {
            "submit": "/submit-artifact",
            "health": "/health",
            "patterns": "/test-patterns"
        },
        "peer_id": PEER_ID,
        "default_behavior": "Always SUCCESS unless a failure pattern is in the title",
        "processing_delay": "300ms"
    }

@app.get("/test-patterns")
async def get_test_patterns():
    """Get available test patterns for automated testing."""
    return {
        "description": "Use these patterns in the artifact title for predictable results. If no pattern is found, the submission will succeed.",
        "failure_patterns": {
            "gas_error": ["test_gas", "force_gas"],
            "network_error": ["test_network", "force_network"],
            "timeout_error": ["test_timeout", "force_timeout"],
            "invalid_data": ["test_invalid", "force_invalid"],
            "general_failure": ["test_fail", "force_fail"],
            "rejected": ["test_rejected", "force_rejected"],
            "example": "Title: 'TEST_GAS - My Document' → Always fails with gas error"
        },
        "default_behavior": "No failure pattern in title → Always SUCCEEDS."
    }

@app.get("/stats")
async def get_stats():
    """Get service statistics."""
    uptime = time.time() - startup_time
    
    return {
        "uptime_seconds": uptime,
        "peer_id": PEER_ID,
        "processing_delay_ms": 300,
        "service_status": "running",
        "test_automation": "enabled",
        "documentation": "/test-patterns"
    }

if __name__ == "__main__":
    logger.info("🚀 Starting Mock Blockchain Peer service...")
    logger.info(f"🔗 Peer ID: {PEER_ID}")
    logger.info("⚡ Test automation enabled - check /test-patterns for usage")
    logger.info("📊 Default behavior: Always SUCCEED unless a failure pattern is in the title.")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info",
        access_log=True
    ) 