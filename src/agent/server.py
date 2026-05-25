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


@app.post("/admin/verify")
async def verify_pipeline():
    """
    Verify the end-to-end SharePoint → Document AI → h2oGPTe RAG pipeline.
    Returns a structured JSON report covering auth, library access, collection
    alignment, document counts, and a test RAG probe per collection.
    """
    from ..integrations.graph_api_client import GraphAPIClient
    from ..rag.sharepoint_sync import SITE_CATEGORY_LIBRARY_MAP
    from ..rag.h2ogpte_client import H2OGPTeClient
    from ..agent.phase_controller import SITE_CATEGORY_COLLECTION_MAP

    report: dict = {
        "sharepoint_auth": None,
        "sharepoint_libraries": {},
        "h2ogpte_connected": None,
        "collections": {},
        "rag_probes": {},
        "warnings": [],
    }

    # Check 1 — SharePoint auth
    graph = GraphAPIClient()
    try:
        token = graph._token()
        report["sharepoint_auth"] = bool(token)
    except Exception as e:
        report["sharepoint_auth"] = False
        report["warnings"].append(f"SharePoint auth failed: {e}")

    # Check 2 — Library accessibility
    if report["sharepoint_auth"]:
        for category, library_id in SITE_CATEGORY_LIBRARY_MAP.items():
            if not library_id:
                report["sharepoint_libraries"][category] = {"error": "env var not set"}
                continue
            try:
                docs = await graph.list_changed_documents(library_id)
                report["sharepoint_libraries"][category] = {"doc_count": len(docs)}
            except Exception as e:
                report["sharepoint_libraries"][category] = {"error": str(e)}

    # Check 3 — h2oGPTe connectivity
    h2o = H2OGPTeClient()
    try:
        live_collections = h2o.client.list_recent_collections(0, 100)
        report["h2ogpte_connected"] = True
    except Exception as e:
        report["h2ogpte_connected"] = False
        report["warnings"].append(f"h2oGPTe connection failed: {e}")
        return report

    # Check 4 — Collection alignment
    live_by_name = {c.name: c for c in live_collections}
    live_by_id   = {c.id:   c for c in live_collections}
    resolved: dict[str, str | None] = {}
    for category, expected_id in SITE_CATEGORY_COLLECTION_MAP.items():
        expected_name = f"collection_{category.lower()}"
        live_col = live_by_id.get(expected_id) or live_by_name.get(expected_name)
        if live_col is None:
            report["collections"][category] = {"found": False, "expected_id": expected_id}
            resolved[category] = None
            continue
        try:
            doc_count = len(h2o.client.list_documents_in_collection(live_col.id, 0, 200))
        except Exception:
            doc_count = -1
        report["collections"][category] = {
            "found": True,
            "id_match": live_col.id == expected_id,
            "live_id": live_col.id,
            "expected_id": expected_id,
            "doc_count": doc_count,
        }
        resolved[category] = live_col.id

    # Check 5 — RAG probe
    probe = "List one typical duty for this site type."
    system_prompt = (
        "You are a JBS assistant. Use the knowledge base. "
        "Output ONLY a single duty name — no preamble."
    )
    for category, collection_id in resolved.items():
        if not collection_id:
            report["rag_probes"][category] = {"skipped": True}
            continue
        try:
            reply, _ = await h2o.chat(
                collection_id=collection_id,
                conversation_id=None,
                message=probe,
                system_prompt=system_prompt,
            )
            report["rag_probes"][category] = {
                "ok": bool(reply.strip()),
                "response_chars": len(reply.strip()),
                "preview": reply.strip()[:120],
            }
        except Exception as e:
            report["rag_probes"][category] = {"ok": False, "error": str(e)}

    return report
