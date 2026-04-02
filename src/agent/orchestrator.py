"""
Conversation Orchestrator
Manages the 2-phase JBS interview state machine.
  Phase 1 (Setup):     Collect Customer Name, Site Name, Site Category, Job Purpose.
                       Handled entirely in CODE — no LLM involved.
                       Binds the RAG collection from site_category.
  Phase 2 (Interview): Full JBS interview — Duties & Tasks, Safety & Compliance,
                       Review & Approval — managed by the LLM in one continuous session.
"""

import os
import time
import httpx
from .state_manager import StateManager
from .phase_controller import PhaseController, SITE_CATEGORY_COLLECTION_MAP, APPROVAL_KEYWORDS
from .prompt_builder import PromptBuilder
from ..rag.h2ogpte_client import H2OGPTeClient

DOCUMENT_GENERATOR_URL = os.environ.get("DOCUMENT_GENERATOR_URL", "http://localhost:8002")

RESET_COMMANDS = {"new jbs", "restart", "reset", "start over", "start again", "new session"}

# Ordered list for numeric site category selection ("1" → Corporate, "2" → Aviation …)
_CATEGORY_LIST = ["Corporate", "Aviation", "Industrial", "Maritime", "Retail"]

# Messages that look like greetings rather than Customer Name answers
_GREETING_TRIGGERS = {
    "hi", "hello", "hey", "start", "begin", "go", "help",
    "ok", "sure", "yo", "g'day", "good morning", "good afternoon",
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


# ---------------------------------------------------------------------------
# Phase 1 — code-driven data collection (no LLM)
# ---------------------------------------------------------------------------

def _parse_category(text: str) -> str:
    """Map user input to a canonical site category name."""
    lower = text.strip().lower()
    # Match by name (handles "2.Aviation", "aviation", etc.)
    for cat in _CATEGORY_LIST:
        if cat.lower() in lower:
            return cat
    # Match by number (1–5)
    for i, cat in enumerate(_CATEGORY_LIST, 1):
        if lower == str(i) or lower.startswith(f"{i}.") or lower.startswith(f"{i} "):
            return cat
    # Return title-cased as-is (accept whatever the user typed)
    return text.strip().title()


_SITE_CATEGORY_PROMPT = (
    "What is the Site Category?\n"
    "1. Corporate\n"
    "2. Aviation\n"
    "3. Industrial\n"
    "4. Maritime\n"
    "5. Retail"
)

_WELCOME = (
    "Welcome! I will guide you through creating a Job Breakdown Statement.\n\n"
    "What is the **Customer Name**? (the organisation that hired Certis)"
)


def _phase1_respond(text: str, session: dict) -> str | None:
    """
    Handle one Phase 1 step entirely in code.

    Returns a reply string for steps 1–5, or None when Phase 1 is confirmed
    (caller should immediately kick off Phase 2 via LLM).
    """
    fields = session.setdefault("collected_fields", {})
    step = session.get("phase1_step", 1)

    if step == 1:
        # If this looks like a greeting, send the welcome prompt and stay on step 1
        if text.strip().lower() in _GREETING_TRIGGERS:
            return _WELCOME
        fields["customer_name"] = text.strip()
        session["phase1_step"] = 2
        return f"Got it — Customer Name: **{fields['customer_name']}**\n\nWhat is the **Site Name**? (the physical location)"

    if step == 2:
        fields["site_name"] = text.strip()
        session["phase1_step"] = 3
        return f"Got it — Site Name: **{fields['site_name']}**\n\n{_SITE_CATEGORY_PROMPT}"

    if step == 3:
        category = _parse_category(text)
        fields["site_category"] = category
        session["collection_id"] = SITE_CATEGORY_COLLECTION_MAP.get(category)
        session["phase1_step"] = 4
        return f"Got it — Site Category: **{category}**\n\nWhat is the **Job Purpose**? (brief description of the role or task)"

    if step == 4:
        fields["job_purpose"] = text.strip()
        session["phase1_step"] = 5
        return (
            f"Got it — Job Purpose: **{fields['job_purpose']}**\n\n"
            "**Summary**\n"
            f"1. Customer Name: {fields['customer_name']}\n"
            f"2. Site Name: {fields['site_name']}\n"
            f"3. Site Category: {fields['site_category']}\n"
            f"4. Job Purpose: {fields['job_purpose']}\n\n"
            "Are these details correct? Reply **Yes** to proceed, "
            "or tell me which field to change (e.g. 'change site name')."
        )

    if step == 5:
        lower = text.lower()
        # Approved — advance to Phase 2
        if any(k in lower for k in APPROVAL_KEYWORDS):
            fields["phase1_confirmed"] = True
            session["phase"] = 2
            return None  # Caller will kick off Phase 2 via LLM

        # User wants to change a specific field — detect which one
        if "customer" in lower:
            session["phase1_step"] = 1
            return "Sure! What is the **Customer Name**?"
        if "site name" in lower or ("site" in lower and "category" not in lower):
            session["phase1_step"] = 2
            return "Sure! What is the **Site Name**?"
        if "category" in lower:
            session["phase1_step"] = 3
            return f"Sure!\n\n{_SITE_CATEGORY_PROMPT}"
        if "purpose" in lower or "job" in lower:
            session["phase1_step"] = 4
            return "Sure! What is the **Job Purpose**?"

        # Unclear — show the summary again and ask
        return (
            "Which field would you like to change?\n"
            f"1. Customer Name: {fields.get('customer_name', '')}\n"
            f"2. Site Name: {fields.get('site_name', '')}\n"
            f"3. Site Category: {fields.get('site_category', '')}\n"
            f"4. Job Purpose: {fields.get('job_purpose', '')}\n\n"
            "Reply 'change customer name', 'change site name', etc. — or **Yes** to confirm."
        )

    # Fallback (should not reach here)
    session["phase1_step"] = 1
    return _WELCOME


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    # Reset: wipe session and send Phase 1 opening question directly
    if msg["text"].strip().lower() in RESET_COMMANDS:
        state_mgr.save(user_id, {})
        await _send_reply(msg, "Starting a new JBS session.\n\nWhat is the **Customer Name**? (the organisation that hired Certis)")
        return

    # Complete session guard
    if session.get("status") == "complete":
        await _send_reply(msg, "This JBS session is complete. Send 'New JBS' to start a new session.")
        return

    current_phase = session.get("phase", 1)

    # -------------------------------------------------------------------------
    # PHASE 1: code-driven — no LLM call
    # -------------------------------------------------------------------------
    if current_phase == 1:
        response = _phase1_respond(msg["text"], session)

        if response is not None:
            # Still collecting Phase 1 data
            state_mgr.save(user_id, session)
            await _send_reply(msg, response)
            return

        # response is None → Phase 1 confirmed; session["phase"] already set to 2
        # Fall through immediately to Phase 2 LLM kickoff (no extra round-trip needed)

    # -------------------------------------------------------------------------
    # PHASE 2: LLM-driven interview (Sections A → B → C)
    # -------------------------------------------------------------------------
    builder = PromptBuilder(session, 2)

    # On the Phase 1→2 transition turn, use a clean kickoff message instead of
    # the user's confirmation text so the LLM starts Phase 2 with clear intent.
    if current_phase == 1:  # just transitioned this turn
        llm_input = (
            "Phase 1 is confirmed. Begin the JBS interview with Section A: "
            "suggest the standard duties for this site."
        )
    else:
        llm_input = msg["text"]

    response_text, conv_id = await _get_h2ogpte().chat(
        collection_id=session.get("collection_id"),
        conversation_id=session.get("h2ogpte_conv_id"),
        message=llm_input,
        system_prompt=builder.system_prompt,
    )
    session["h2ogpte_conv_id"] = conv_id

    # Store turn (phase 2)
    turns = session.setdefault("turns", [])
    turns.append({"phase": 2, "user": msg["text"], "assistant": response_text})
    session["turns"] = turns[-30:]

    # Detect final approval → trigger document generation
    phase_ctrl = PhaseController(session)
    if phase_ctrl.is_approved(msg["text"]) and session.get("phase") == 2:
        try:
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
        except Exception:
            # Document generation failed — don't mark complete, let user retry
            pass

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
    """Reply to a Teams message using the Bot Framework REST API."""
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
    """Obtain a cached OAuth2 token for the Bot Framework API."""
    now = time.time()
    if _bot_token_cache["token"] and now < _bot_token_cache["expires_at"]:
        return _bot_token_cache["token"]

    app_id       = os.environ["TEAMS_APP_ID"]
    app_password = os.environ["TEAMS_APP_PASSWORD"]
    tenant_id    = os.environ.get("AZURE_TENANT_ID", "botframework.com")

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
