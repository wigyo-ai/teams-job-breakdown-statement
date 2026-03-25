"""
Webhook Service — public-facing FastAPI app.
Receives signed events from WhatsApp and Telegram,
validates signatures, normalises to internal schema,
and forwards to the internal Orchestrator service.
"""

import hmac
import hashlib
import os
import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from typing import Optional
from .schema import NormalisedMessage
from .whatsapp import parse_whatsapp_event
from .telegram import parse_telegram_event

app = FastAPI(title="JBS Webhook Service", version="1.0.0")
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None,
):
    """Meta webhook verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == os.environ.get("WHATSAPP_VERIFY_TOKEN"):
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
):
    body = await request.body()
    _verify_whatsapp_signature(body, x_hub_signature_256)
    payload = await request.json()
    msg = parse_whatsapp_event(payload)
    if msg:
        await _forward(msg)
    return {"status": "ok"}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    payload = await request.json()
    msg = parse_telegram_event(payload)
    if msg:
        await _forward(msg)
    return {"status": "ok"}


def _verify_whatsapp_signature(body: bytes, signature: str):
    secret = os.environ["WHATSAPP_APP_SECRET"]
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise HTTPException(status_code=403, detail="Invalid signature")


async def _forward(msg: NormalisedMessage):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{ORCHESTRATOR_URL}/process",
            json=msg.dict(),
            timeout=30,
        )
