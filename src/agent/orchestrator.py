"""
Conversation Orchestrator
Manages the 5-phase JBS interview state machine.
Delegates LLM inference + conversation history to h2oGPTe.
Phase state + collected fields stored in StateManager (memory/sqlite/external_redis).
"""

import os
import time
import httpx
from .state_manager import StateManager
from .phase_controller import PhaseController
from .prompt_builder import PromptBuilder
from ..rag.h2ogpte_client import H2OGPTeClient
from ..integrations.mozart_client import MozartClient
from ..document.generator import DocumentGenerator

state_mgr = StateManager()
h2ogpte   = H2OGPTeClient()
mozart    = MozartClient()
doc_gen   = DocumentGenerator()

# Simple in-process cache for the Bot Framework OAuth token
_bot_token_cache: dict = {"token": None, "expires_at": 0}


async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    # Guard: ignore messages for completed sessions
    if session.get("status") == "complete":
        await _send_reply(msg, "This JBS session is complete. Start a new session by sending 'New JBS'.")
        return

    phase_ctrl = PhaseController(session)
    phase_ctrl.ingest_user_input(msg["text"])

    # Build the phase-specific system prompt
    builder = PromptBuilder(session, phase_ctrl.current_phase)

    # h2oGPTe manages full turn history via conversation_id
    # On first turn, create a new conversation and store the ID
    response_text, conv_id = await h2ogpte.chat(
        collection_id=session.get("collection_id"),
        conversation_id=session.get("h2ogpte_conv_id"),
        message=msg["text"],
        system_prompt=builder.system_prompt
    )
    session["h2ogpte_conv_id"] = conv_id

    # Phase 4: Mozart reference enrichment
    if phase_ctrl.current_phase == 4:
        mozart_site_id = phase_ctrl.extract_mozart_site_id(msg["text"])
        if mozart_site_id:
            session["collected_fields"]["mozart_site_id"] = mozart_site_id
            refs = await mozart.get_references(mozart_site_id)
            session["mozart_references"] = refs

    # Phase 5: detect user approval and trigger document generation
    if phase_ctrl.current_phase == 5 and phase_ctrl.is_approved(msg["text"]):
        jbs_json = phase_ctrl.build_jbs_json(session)
        doc_url  = await doc_gen.generate(jbs_json)
        response_text = (
            "Your JBS document has been generated and is ready for download.\n\n"
            f"Download link (valid 15 minutes):\n{doc_url}"
        )
        session["status"] = "complete"

    # Advance phase if all required fields for current phase are collected
    phase_ctrl.advance_if_complete()

    state_mgr.save(user_id, session)
    await _send_reply(msg, response_text)


async def _send_reply(msg: dict, text: str):
    if msg["channel"] == "teams":
        await _send_teams(
            service_url=msg["service_url"],
            conversation_id=msg["conversation_id"],
            reply_to_id=msg["reply_to"],
            text=text,
        )


async def _send_teams(service_url: str, conversation_id: str, reply_to_id: str, text: str):
    """
    Reply to a Teams message using the Bot Framework REST API.

    Bot Framework reply URL pattern:
      {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}

    The access token is obtained from Azure AD using the bot's client credentials
    and cached for the duration of its TTL (typically 3600 seconds).
    """
    token = await _get_bot_token()
    url = (
        f"{service_url.rstrip('/')}/v3/conversations"
        f"/{conversation_id}/activities/{reply_to_id}"
    )
    async with httpx.AsyncClient() as client:
        await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"type": "message", "text": text},
        )


async def _get_bot_token() -> str:
    """
    Obtain an OAuth2 access token scoped to the Bot Framework API.

    Uses the bot's App ID and App Password (client credentials flow).
    The token is cached in memory until 60 seconds before expiry.
    """
    now = time.time()
    if _bot_token_cache["token"] and now < _bot_token_cache["expires_at"]:
        return _bot_token_cache["token"]

    app_id       = os.environ["TEAMS_APP_ID"]
    app_password = os.environ["TEAMS_APP_PASSWORD"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     app_id,
                "client_secret": app_password,
                "scope":         "https://api.botframework.com/.default",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _bot_token_cache["token"]      = data["access_token"]
    _bot_token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return _bot_token_cache["token"]
