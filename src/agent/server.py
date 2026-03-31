"""
Orchestrator HTTP server — internal only, not publicly exposed.
Called by the Webhook Service after signature validation.
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from .orchestrator import process_message
from .state_manager import StateManager

app = FastAPI(title="JBS Orchestrator", version="1.0.0")
state_mgr = StateManager()


class InboundMessage(BaseModel):
    channel:         str
    user_id:         str
    user_name:       Optional[str] = None
    text:            str
    timestamp:       str
    reply_to:        str
    service_url:     Optional[str] = None
    conversation_id: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/process")
async def process(msg: InboundMessage):
    await process_message(msg.dict())
    return {"status": "ok"}


@app.get("/sessions")
async def list_sessions():
    """Used by the Wave admin dashboard to show active interviews."""
    return state_mgr.list_all()


@app.post("/admin/sync")
async def trigger_sync():
    """Trigger SharePoint → h2oGPTe sync on demand (called by Wave dashboard)."""
    import asyncio
    from ..rag.sharepoint_sync import sync_sharepoint_to_h2ogpte
    asyncio.create_task(sync_sharepoint_to_h2ogpte())
    return {"status": "sync_started"}
