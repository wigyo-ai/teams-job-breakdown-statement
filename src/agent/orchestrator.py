"""
Conversation Orchestrator
Manages the 4-phase JBS interview state machine.
Delegates LLM inference + conversation history to h2oGPTe.
Phase state + collected fields stored in StateManager (memory/sqlite/external_redis).
Document generation is performed by the Document Generator service via HTTP.
"""

import os
import time
import httpx
from .state_manager import StateManager
from .phase_controller import PhaseController
from .prompt_builder import PromptBuilder
from ..rag.h2ogpte_client import H2OGPTeClient

DOCUMENT_GENERATOR_URL = os.environ.get("DOCUMENT_GENERATOR_URL", "http://localhost:8002")

# Any of these phrases reset the session to a clean slate
RESET_COMMANDS = {"new jbs", "restart", "reset", "start over", "start again", "new session"}

# When the phase advances, send this synthetic message to the LLM instead of
# the user's confirmation text.  The confirmation text ("they are correct, proceed")
# makes no sense as the first message in a new phase context and causes the LLM
# to restart Phase 1.  A clear kickoff message removes all ambiguity.
PHASE_KICKOFF = {
    2: "Phase 1 is confirmed. Begin Phase 2 now: suggest the standard duties for this site based on the Site Category and Job Purpose shown in the context.",
    3: "Phase 2 is confirmed. Begin Phase 3 now: ask about site hazards and safety requirements.",
    4: "Phase 3 is confirmed. Begin Phase 4 now: present the full JBS summary for review and approval.",
}

state_mgr = StateManager()
_h2ogpte: H2OGPTeClient | None = None


def _get_h2ogpte() -> H2OGPTeClient:
    global _h2ogpte
    if _h2ogpte is None:
        _h2ogpte = H2OGPTeClient()
    return _h2ogpte

# Simple in-process cache for the Bot Framework OAuth token
_bot_token_cache: dict = {"token": None, "expires_at": 0}


async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    # Reset command: wipe the session entirely and start Phase 1 fresh.
    # Must be checked before anything else so a stale Phase 2/3/4 session
    # cannot interfere when the user asks to restart.
    if msg["text"].strip().lower() in RESET_COMMANDS:
        state_mgr.save(user_id, {})
        await _send_reply(msg, "Starting a new JBS session.\n\nWhat is the Customer Name? (the organisation that hired Certis)")
        return

    # Guard: ignore messages for completed sessions
    if session.get("status") == "complete":
        await _send_reply(msg, "This JBS session is complete. Send 'New JBS' to start a new session.")
        return

    phase_ctrl = PhaseController(session)
    prior_phase = phase_ctrl.current_phase
    phase_ctrl.ingest_user_input(msg["text"])

    # Advance phase BEFORE building the prompt so the LLM immediately uses the
    # next phase's instructions on the same turn the user triggers the transition.
    if phase_ctrl.current_phase < 4:
        phase_ctrl.advance_if_complete()

    just_advanced = phase_ctrl.current_phase != prior_phase

    # Build the phase-specific system prompt (uses updated phase after advance)
    builder = PromptBuilder(session, phase_ctrl.current_phase)

    # On a phase transition, replace the user's confirmation text with a clear
    # kickoff message so the LLM doesn't try to interpret "they are correct,
    # proceed" inside a brand-new phase context with no prior history.
    llm_input = PHASE_KICKOFF.get(phase_ctrl.current_phase, msg["text"]) if just_advanced else msg["text"]

    response_text, conv_id = await _get_h2ogpte().chat(
        collection_id=session.get("collection_id"),
        conversation_id=session.get("h2ogpte_conv_id"),
        message=llm_input,
        system_prompt=builder.system_prompt
    )
    session["h2ogpte_conv_id"] = conv_id

    # Store turn so the next system prompt includes full conversation history
    turns = session.setdefault("turns", [])
    # Tag each turn with the phase it belongs to so prompt_builder can
    # inject only the turns relevant to the current phase, preventing the LLM
    # from being confused by old exchanges from earlier phases.
    turns.append({"phase": phase_ctrl.current_phase, "user": msg["text"], "assistant": response_text})
    session["turns"] = turns[-30:]  # keep enough history across phases

    # Phase 4: detect user approval and trigger document generation
    if phase_ctrl.current_phase == 4 and phase_ctrl.is_approved(msg["text"]):
        jbs_json = phase_ctrl.build_jbs_json(session)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DOCUMENT_GENERATOR_URL}/generate",
                json={"jbs_json": jbs_json},
                timeout=60,
            )
            resp.raise_for_status()
            doc_url = resp.json()["download_url"]
        response_text = (
            "Your JBS document has been generated and is ready for download.\n\n"
            f"Download link (valid 15 minutes):\n{doc_url}"
        )
        session["status"] = "complete"

    # Save the (possibly advanced) phase back to session
    session["phase"] = phase_ctrl.current_phase
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
            json={
                "type": "message",
                "text": text,
                "from": {"id": os.environ["TEAMS_APP_ID"], "name": "JBS Assistant"},
            },
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

    tenant_id = os.environ.get("AZURE_TENANT_ID", "botframework.com")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
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
