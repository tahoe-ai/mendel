from __future__ import annotations

import asyncio
import logging
import os

from fastapi import Depends, FastAPI, HTTPException

from ..core.orchestrator import scan_all_targets
from .auth import verify_oidc

logging.basicConfig(
    level=os.environ.get("PATCHBOT_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="patch-bot")
_scan_lock = asyncio.Lock()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/scan", dependencies=[Depends(verify_oidc)])
async def scan() -> dict:
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="scan already in progress")
    async with _scan_lock:
        try:
            return await scan_all_targets()
        except Exception as e:
            log.exception("scan failed")
            raise HTTPException(status_code=500, detail=str(e)[:500]) from e
