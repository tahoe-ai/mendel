from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

log = logging.getLogger(__name__)


async def verify_oidc(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Verify the incoming request carries a valid Google OIDC token issued
    to our scheduler service account, with audience matching this service's URL."""
    if os.environ.get("PATCHBOT_AUTH_DISABLE") == "1":
        # Local dev / tests
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    expected_audience = os.environ.get("PATCHBOT_OIDC_AUDIENCE") or str(request.url)
    expected_sa = os.environ.get("PATCHBOT_SCHEDULER_SA")  # e.g. patchbot-scheduler@proj.iam.gserviceaccount.com

    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=expected_audience
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"invalid OIDC token: {e}") from e

    if expected_sa and claims.get("email") != expected_sa:
        raise HTTPException(status_code=403, detail=f"unexpected caller {claims.get('email')!r}")
    if not claims.get("email_verified"):
        raise HTTPException(status_code=403, detail="OIDC email_verified=false")
