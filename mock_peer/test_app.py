import pytest
from fastapi.testclient import TestClient
from mock_peer.app import app

@pytest.fixture(scope="module")
def client():
    """
    A pytest fixture that creates a TestClient for the FastAPI app.
    The scope="module" means this will only be created once for all tests in this file.
    """
    with TestClient(app) as c:
        yield c

# --- Test Data ---
SUCCESS_ARTIFACT = {
    "artifactId": "c1a4e3b2-4c1f-4f3b-8c1a-4e3b2a1b4c1f",
    "data": {
        "title": "A standard successful artifact",
        "description": "This should pass without any special patterns."
    },
    "timestamp": "2023-01-01T12:00:00Z"
}

# --- Unit Tests for Endpoints ---

def test_health_check(client: TestClient):
    """Tests if the /health endpoint returns a 200 OK status."""
    response = client.get("/health")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "healthy"
    assert json_response["service"] == "mock-blockchain-peer"

def test_root_endpoint(client: TestClient):
    """Tests the root endpoint for basic service info."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Mock Blockchain Peer" in response.json()["service"]

def test_test_patterns_endpoint(client: TestClient):
    """Tests the /test-patterns endpoint to ensure it returns the correct documentation."""
    response = client.get("/test-patterns")
    assert response.status_code == 200
    json_response = response.json()
    assert "failure_patterns" in json_response
    assert "default_behavior" in json_response
    assert "Always SUCCEEDS" in json_response["default_behavior"]

def test_submission_defaults_to_success(client: TestClient):
    """
    Tests that a standard artifact submission without any failure patterns in the title
    defaults to a successful response.
    """
    response = client.post("/submit-artifact", json=SUCCESS_ARTIFACT)
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["success"] is True
    assert json_response["txId"] is not None
    assert json_response["error"] is None

# --- Parameterized Tests for Failure Scenarios ---

# This list contains tuples of (pattern, expected_error_snippet)
# It allows us to test all failure cases with a single test function.
failure_test_cases = [
    ("test_gas", "gas fees"),
    ("force_network", "Network congestion"),
    ("test_timeout", "timeout"),
    ("force_invalid", "Invalid artifact data format"),
    ("test_fail", "temporarily unavailable"),
    ("force_rejected", "rejected by blockchain"),
]

@pytest.mark.parametrize("pattern, expected_error", failure_test_cases)
def test_submission_failure_patterns(client: TestClient, pattern, expected_error):
    """
    Tests various failure patterns in the artifact title.
    This test is parameterized to run once for each case in `failure_test_cases`.
    """
    artifact_with_failure = {
        "artifactId": "d3f2a1b4-c1f4-4f3b-8c1a-4e3b2a1b4c1f",
        "data": {
            "title": f"This is a test with a {pattern} failure."
        },
        "timestamp": "2023-01-01T12:00:00Z"
    }
    response = client.post("/submit-artifact", json=artifact_with_failure)
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["success"] is False
    assert json_response["txId"] is None
    assert expected_error in json_response["error"]

def test_submission_with_empty_title(client: TestClient):
    """Tests that an artifact with a missing or empty title still succeeds."""
    artifact_no_title = {
        "artifactId": "e4b3c2a1-b4c1-4f3b-8c1a-4e3b2a1b4c1f",
        "data": {
            "description": "This artifact has no title."
        },
        "timestamp": "2023-01-01T12:00:00Z"
    }
    response = client.post("/submit-artifact", json=artifact_no_title)
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["success"] is True
    assert json_response["txId"] is not None

# To run these tests:
# 1. Make sure you have pytest and pytest-cov installed:
#    pip install pytest pytest-cov requests
#
# 2. From the root of the project, run:
#    pytest --cov=mock_peer mock_peer/ 