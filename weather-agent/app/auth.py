"""XSUAA JWT validation middleware — reads credentials from VCAP_SERVICES xsuaa binding."""
from __future__ import annotations

import json
import logging
import os
from typing import Callable

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _xsuaa_creds() -> dict:
    vcap = json.loads(os.environ.get("VCAP_SERVICES", "{}"))
    bindings = vcap.get("xsuaa", [])
    if not bindings:
        raise RuntimeError("No xsuaa binding found in VCAP_SERVICES")
    return bindings[0]["credentials"]


class XSUAAAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        creds = _xsuaa_creds()
        self._audience = creds["xsappname"]
        # Older XSUAA plans use verificationkey instead of jwks_uri;
        # fall back to /token_keys endpoint on the auth server.
        self._jwks_uri = creds.get("jwks_uri") or (
            creds["url"].rstrip("/") + "/token_keys"
        )
        self._jwks_client = PyJWKClient(self._jwks_uri)

    async def dispatch(self, request: Request, call_next: Callable):
        # Agent discovery and health check must be public (CF health check + Joule discovery)
        if request.url.path in ("/.well-known/agent-card.json", "/health"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            logger.warning("JWT rejected: missing or malformed Authorization header")
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            token = auth[7:]
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            decoded = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"verify_aud": False},  # XSUAA aud can be a list or string
            )
            # Manual audience check — XSUAA tokens carry both "sb-<xsappname>" and "<xsappname>"
            aud = decoded.get("aud", [])
            if isinstance(aud, str):
                aud = [aud]
            allowed = {self._audience, f"sb-{self._audience}"}
            if not allowed.intersection(aud):
                logger.warning("JWT audience mismatch: token has %s, expected one of %s", aud, allowed)
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        except Exception as exc:
            logger.warning("JWT validation failed: %s", exc)
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
