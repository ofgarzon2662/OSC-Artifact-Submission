import os
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from cachetools import TTLCache
import httpx


BRIDGE_URL = os.getenv("BRIDGE_URL", "fabric-bridge")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))
CACHE_MAX_ARTIFACTS = int(os.getenv("CACHE_MAX_ARTIFACTS", "100"))
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "500"))


app = FastAPI(title="GetHistory Worker", version="0.1.0")

# Cache structure: { artifactId: { 'asc': list, 'desc': list, 'total': int, 'fetchedAt': float } }
cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=CACHE_MAX_ARTIFACTS, ttl=CACHE_TTL_SECONDS)


class HistoryItem(BaseModel):
    txId: str
    timestamp: Any
    isDelete: bool
    value: Optional[Dict[str, Any]] = None


def _validate_uuid_lower(uuid_str: str) -> str:
    import re
    uuid_regex = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    s = uuid_str.strip().lower()
    if not uuid_regex.match(s):
        raise HTTPException(status_code=400, detail="Invalid artifactId format (lowercase UUID expected)")
    return s


async def fetch_history_from_bridge(artifact_id: str) -> List[Dict[str, Any]]:
    url = f"{BRIDGE_URL}/history/{artifact_id}"
    timeout = httpx.Timeout(10.0, read=20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
        if r.status_code != 200:
            # Bridge may return structured error JSON
            try:
                err = r.json()
            except Exception:
                err = {"message": r.text}
            raise HTTPException(status_code=502, detail={"bridge_status": r.status_code, "error": err})
        try:
            data = r.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to parse bridge response: {e}")
        if not isinstance(data, list):
            raise HTTPException(status_code=502, detail="Bridge returned non-list history payload")
        return data


def get_cached_or_fetch(artifact_id: str, order: str) -> Dict[str, Any]:
    now = time.time()
    entry = cache.get(artifact_id)
    if entry is None:
        # Caller is async; but cache function is sync. We'll fetch outside.
        raise KeyError
    # Ensure both orders are present
    if order == "desc" and entry.get("desc") is None:
        entry["desc"] = list(reversed(entry["asc"]))
    return entry


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "healthy", "service": "get-history-worker"}


@app.get("/history")
async def get_history(
    artifactId: str = Query(..., alias="artifactId"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    includeValue: bool = Query(True, alias="includeValue")
):
    artifact_id = _validate_uuid_lower(artifactId)
    if limit > MAX_PAGE_SIZE:
        limit = MAX_PAGE_SIZE

    # Try cache first
    try:
        entry = get_cached_or_fetch(artifact_id, order)
    except KeyError:
        # Fetch from bridge
        data = await fetch_history_from_bridge(artifact_id)
        # Normalize expected keys and store as asc
        asc = data
        cache[artifact_id] = {
            "asc": asc,
            "desc": None,
            "total": len(asc),
            "fetchedAt": time.time()
        }
        entry = get_cached_or_fetch(artifact_id, order)

    items: List[Dict[str, Any]] = entry[order]
    total = entry["total"]

    if offset >= total:
        page = []
    else:
        page = items[offset: offset + limit]

    if not includeValue:
        page = [{
            "txId": it.get("txId"),
            "timestamp": it.get("timestamp"),
            "isDelete": it.get("isDelete")
        } for it in page]

    return JSONResponse({
        "artifactId": artifact_id,
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "order": order,
        "hasMore": (offset + limit) < total
    })


@app.api_route("/history/refresh", methods=["GET", "POST"])
async def refresh_history(artifactId: str = Query(..., alias="artifactId")):
    artifact_id = _validate_uuid_lower(artifactId)
    data = await fetch_history_from_bridge(artifact_id)
    cache[artifact_id] = {
        "asc": data,
        "desc": None,
        "total": len(data),
        "fetchedAt": time.time()
    }
    return {"artifactId": artifact_id, "total": len(data)}


