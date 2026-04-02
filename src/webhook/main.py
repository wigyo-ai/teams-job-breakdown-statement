"""
Webhook Service — public-facing FastAPI app.
Receives Bot Framework Activity events from Microsoft Teams via Azure Bot Service,
validates the JWT bearer token, normalises to the internal schema,
and forwards to the internal Orchestrator service.
"""

import os
import httpx
import jwt
from jwt import PyJWKClient
from fastapi import FastAPI, Request, HTTPException
from .schema import NormalisedMessage
from .teams import parse_teams_event

app = FastAPI(title="JBS Webhook Service", version="2.0.0")

ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]
TEAMS_APP_ID     = os.environ["TEAMS_APP_ID"]

# Bot Framework publishes its signing keys at this well-known JWKS endpoint.
# PyJWKClient caches the key set in memory to avoid repeated network calls.
_jwks_client = PyJWKClient(
    "https://login.botframework.com/v1/.well-known/keys",
    cache_keys=True,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/teams")
async def teams_webhook(request: Request):
    """
    Receive a Bot Framework Activity from Microsoft Teams.

    Azure Bot Service signs every outbound request with a JWT Bearer token.
    The token is validated before the payload is processed.
    """
    auth_header = request.headers.get("Authorization", "")
    _verify_teams_token(auth_header)

    payload = await request.json()
    msg = parse_teams_event(payload)
    if msg:
        await _forward(msg)
    return {}


def _verify_teams_token(auth_header: str):
    """
    Validate the JWT Bearer token issued by Azure Bot Service.

    Tokens are RS256-signed and must:
      - Have audience equal to this bot's TEAMS_APP_ID
      - Have issuer "https://api.botframework.com"
    """
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header[7:]
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=TEAMS_APP_ID,
            issuer="https://api.botframework.com",
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


async def _forward(msg: NormalisedMessage):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{ORCHESTRATOR_URL}/process",
            json=msg.dict(),
            timeout=150,
        )
