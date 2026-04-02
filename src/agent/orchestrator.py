"""
Conversation Orchestrator — 2-phase JBS state machine.

Phase 1 (Setup): Collect Customer Name, Site Name, Site Category, Job Purpose.
  Handled entirely in CODE. No LLM. Bulletproof step counter.

Phase 2 (Interview): Hybrid LLM+RAG suggestions + code-driven storage.
  LLM+RAG (h2oGPTe) is called ONCE per section to suggest duties, tasks, and
  safety requirements from Certis SOPs. Code then asks the user to confirm or
  modify. The confirmed answer is stored directly in collected_fields — no
  parsing of LLM narrative output into structured data.

  This guarantees that build_jbs_json() always produces a fully-populated
  document rather than blank tables.
"""

import os
import re
import time
import httpx
from .state_manager import StateManager
from .phase_controller import PhaseController, SITE_CATEGORY_COLLECTION_MAP, APPROVAL_KEYWORDS
from ..rag.h2ogpte_client import H2OGPTeClient

DOCUMENT_GENERATOR_URL = os.environ.get("DOCUMENT_GENERATOR_URL", "http://localhost:8002")

RESET_COMMANDS = {"new jbs", "restart", "reset", "start over", "start again", "new session"}

_CATEGORY_LIST = ["Corporate", "Aviation", "Industrial", "Maritime", "Retail"]
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


_bot_token_cache: dict = {"token": None, "expires_at": 0}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — Code-driven data collection (no LLM)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_category(text: str) -> str:
    lower = text.strip().lower()
    for cat in _CATEGORY_LIST:
        if cat.lower() in lower:
            return cat
    for i, cat in enumerate(_CATEGORY_LIST, 1):
        if lower == str(i) or lower.startswith(f"{i}.") or lower.startswith(f"{i} "):
            return cat
    return text.strip().title()


_SITE_CATEGORY_PROMPT = (
    "What is the Site Category?\n"
    "1. Corporate\n2. Aviation\n3. Industrial\n4. Maritime\n5. Retail"
)

_WELCOME = (
    "Welcome! I will guide you through creating a Job Breakdown Statement.\n\n"
    "What is the **Customer Name**? (the organisation that hired Certis)"
)


def _phase1_respond(text: str, session: dict) -> str | None:
    """
    Handle one Phase 1 step entirely in code.
    Returns a reply string, or None when Phase 1 is confirmed (advance to Phase 2).
    """
    fields = session.setdefault("collected_fields", {})
    step = session.get("phase1_step", 1)

    if step == 1:
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
        if any(k in lower for k in APPROVAL_KEYWORDS):
            fields["phase1_confirmed"] = True
            session["phase"] = 2
            return None  # Trigger Phase 2

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

        return (
            "Which field would you like to change?\n"
            f"1. Customer Name: {fields.get('customer_name', '')}\n"
            f"2. Site Name: {fields.get('site_name', '')}\n"
            f"3. Site Category: {fields.get('site_category', '')}\n"
            f"4. Job Purpose: {fields.get('job_purpose', '')}\n\n"
            "Reply 'change customer name', 'change site name', etc. — or **Yes** to confirm."
        )

    session["phase1_step"] = 1
    return _WELCOME


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — LLM+RAG suggestions, code-driven storage
# ─────────────────────────────────────────────────────────────────────────────

async def _get_rag_suggestion(prompt: str, collection_id: str | None) -> str:
    """
    Single-shot RAG query using h2oGPTe.
    Each call is independent (conversation_id=None) to avoid stale history confusion.
    """
    system_prompt = (
        "You are a Certis JBS assistant with access to Certis security SOPs and procedures. "
        "Use the knowledge base to provide accurate, site-specific suggestions. "
        "Output ONLY in the requested format — no preamble, no extra explanation."
    )
    result, _ = await _get_h2ogpte().chat(
        collection_id=collection_id,
        conversation_id=None,
        message=prompt,
        system_prompt=system_prompt,
    )
    return result.strip()


def _parse_numbered_list(text: str) -> list[str]:
    """Parse a numbered/bulleted list into clean items."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    result = []
    for line in lines:
        cleaned = re.sub(r'^[\d]+[.)]\s*|^[-•*]\s*', '', line).strip()
        if cleaned:
            result.append(cleaned)
    return result


def _parse_list(text: str) -> list[str]:
    """Split on newlines or commas; strip numbers/bullets."""
    if '\n' in text:
        items = [l.strip() for l in text.splitlines() if l.strip()]
    else:
        items = [x.strip() for x in text.split(',') if x.strip()]
    items = [re.sub(r'^[\d]+[.)]\s*|^[-•*]\s*', '', i).strip() for i in items]
    return [i for i in items if i]


def _parse_task_line(line: str, default_role: str) -> dict:
    """Parse 'Task description | Frequency | Role' or just 'Task description'."""
    line = re.sub(r'^[\d]+[.)]\s*|^[-•*]\s*', '', line).strip()
    parts = [p.strip() for p in line.split('|')]
    return {
        "task_description": parts[0] if parts else line,
        "trigger":          "As required",
        "frequency":        parts[1] if len(parts) > 1 else "As required",
        "responsible_role": parts[2] if len(parts) > 2 else default_role,
    }


def _is_confirmation(text: str) -> bool:
    lower = text.strip().lower()
    CONFIRM_WORDS = {
        "yes", "confirm", "ok", "okay", "correct", "looks good", "approved",
        "approve", "proceed", "confirmed", "all good", "good", "right",
        "use these", "that's correct", "thats correct",
    }
    return lower in CONFIRM_WORDS or lower.startswith("yes") or lower.startswith("confirm")


def _extract_safety_from_suggestion(raw: str) -> tuple[list, list]:
    """Extract hazards and PPE lists from LLM suggestion output."""
    hazards, ppe = [], []
    for line in raw.splitlines():
        line = line.strip()
        if re.match(r'hazards?\s*:', line, re.IGNORECASE):
            hazards = _parse_list(re.split(r':', line, 1)[1])
        elif re.match(r'ppe\s*:', line, re.IGNORECASE):
            ppe = _parse_list(re.split(r':', line, 1)[1])
    return hazards, ppe


def _parse_safety_response(
    text: str,
    suggestion_hazards: list,
    suggestion_ppe: list,
) -> tuple[list, list]:
    """Return (hazards, ppe) from user text, falling back to suggestions if confirming."""
    if _is_confirmation(text):
        return suggestion_hazards, suggestion_ppe

    hazards = suggestion_hazards
    ppe = suggestion_ppe

    hazard_match = re.search(r'hazards?\s*:(.+?)(?=\bppe\b|$)', text, re.IGNORECASE | re.DOTALL)
    if hazard_match:
        hazards = _parse_list(hazard_match.group(1))

    ppe_match = re.search(r'ppe\s*:(.+?)$', text, re.IGNORECASE | re.DOTALL)
    if ppe_match:
        ppe = _parse_list(ppe_match.group(1))

    return hazards, ppe


def _build_summary(fields: dict) -> str:
    """Build a formatted JBS summary from collected_fields."""
    lines = [
        "**JBS SUMMARY**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"**Customer:** {fields.get('customer_name', '')}",
        f"**Site:** {fields.get('site_name', '')} ({fields.get('site_category', '')})",
        f"**Job Purpose:** {fields.get('job_purpose', '')}",
        "",
        "**DUTIES & TASKS**",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for i, duty in enumerate(fields.get("duties", []), 1):
        lines.append(f"\n**{i}. {duty['duty_name']}**")
        for task in duty.get("tasks", []):
            freq = task.get("frequency", "As required")
            role = task.get("responsible_role", "")
            lines.append(f"   • {task['task_description']} — {freq} — {role}")

    sc = fields.get("safety_compliance", {})
    lines += [
        "",
        "**SAFETY & COMPLIANCE**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Hazards: {', '.join(sc.get('site_hazards', []))}",
        f"PPE: {', '.join(sc.get('ppe_requirements', []))}",
        "",
        "Type **APPROVE** to generate the document.",
        "Or say **change duties**, **change safety**, or name a specific duty to edit.",
    ]
    return "\n".join(lines)


async def _phase2_respond(text: str, session: dict) -> str | None:
    """
    Handle Phase 2 interaction.
    Returns a reply string, or None to signal document generation should proceed.

    Uses a while loop to handle silent state transitions (e.g. storing confirmed
    duties then immediately fetching the next LLM suggestion) without extra
    round-trips from the user.
    """
    fields = session.setdefault("collected_fields", {})
    collection_id = session.get("collection_id")

    while True:
        step = session.get("p2_step", "suggest_duties")

        # ── SUGGEST DUTIES ─────────────────────────────────────────────────
        if step == "suggest_duties":
            prompt = (
                f"List 3-5 typical duties for a {fields['job_purpose']} "
                f"at a {fields['site_category']} site. "
                "Output ONLY a numbered list of duty names, one per line."
            )
            raw = await _get_rag_suggestion(prompt, collection_id)
            duty_list = _parse_numbered_list(raw)
            session["p2_suggestions"] = {"duties": duty_list}
            session["p2_step"] = "confirm_duties"

            numbered = "\n".join(f"{i+1}. {d}" for i, d in enumerate(duty_list))
            return (
                f"Based on the Certis knowledge base, here are suggested duties for "
                f"a **{fields['job_purpose']}** at a **{fields['site_category']}** site:\n\n"
                f"{numbered}\n\n"
                "Reply **confirm** to use these, or type your own duties (one per line)."
            )

        # ── CONFIRM DUTIES ─────────────────────────────────────────────────
        elif step == "confirm_duties":
            duties = (
                session["p2_suggestions"]["duties"]
                if _is_confirmation(text)
                else _parse_list(text)
            )
            if not duties:
                return "Please list at least one duty (one per line)."

            fields["duties"] = [{"duty_name": d, "tasks": []} for d in duties]
            session["p2_step"] = "suggest_tasks_0"
            text = ""
            continue

        # ── SUGGEST TASKS (per duty) ───────────────────────────────────────
        elif step.startswith("suggest_tasks_"):
            idx = int(step.rsplit("_", 1)[-1])
            duty_name = fields["duties"][idx]["duty_name"]
            prompt = (
                f"List 3-5 typical tasks for the duty '{duty_name}' "
                f"in a {fields['job_purpose']} role at a {fields['site_category']} site. "
                "For each task output in this exact format: "
                "task description | frequency | responsible role\n"
                "Output ONLY a numbered list. Example:\n"
                "1. Check visitor IDs | Per visitor | Security Officer"
            )
            raw = await _get_rag_suggestion(prompt, collection_id)
            session["p2_suggestions"] = {"tasks": raw}
            session["p2_step"] = f"confirm_tasks_{idx}"

            return (
                f"Suggested tasks for **{duty_name}**:\n\n{raw}\n\n"
                "Reply **confirm** to use these, or type your own tasks "
                "(one per line, format: `Task | Frequency | Role`)."
            )

        # ── CONFIRM TASKS (per duty) ───────────────────────────────────────
        elif step.startswith("confirm_tasks_"):
            idx = int(step.rsplit("_", 1)[-1])

            if _is_confirmation(text):
                task_lines = _parse_numbered_list(session["p2_suggestions"]["tasks"])
            else:
                task_lines = _parse_list(text)

            default_role = fields.get("job_purpose", "Security Officer")
            tasks = []
            for seq, line in enumerate(task_lines, 1):
                task = _parse_task_line(line, default_role)
                task["sequence"] = seq
                tasks.append(task)
            fields["duties"][idx]["tasks"] = tasks

            next_idx = idx + 1
            if next_idx < len(fields["duties"]):
                session["p2_step"] = f"suggest_tasks_{next_idx}"
            else:
                session["p2_step"] = "suggest_safety"
            text = ""
            continue

        # ── SUGGEST SAFETY ─────────────────────────────────────────────────
        elif step == "suggest_safety":
            prompt = (
                f"For a {fields['site_category']} site at {fields['site_name']}, list:\n"
                "1. Typical site hazards\n2. Required PPE\n"
                "Format your response exactly as:\n"
                "Hazards: item1, item2, item3\n"
                "PPE: item1, item2, item3"
            )
            raw = await _get_rag_suggestion(prompt, collection_id)
            hazards_s, ppe_s = _extract_safety_from_suggestion(raw)
            session["p2_suggestions"] = {"safety_raw": raw, "hazards": hazards_s, "ppe": ppe_s}
            session["p2_step"] = "confirm_safety"

            return (
                f"Suggested safety requirements:\n\n{raw}\n\n"
                "Reply **confirm** to use these, or provide your own:\n"
                "**Hazards:** [comma-separated list]\n"
                "**PPE:** [comma-separated list]"
            )

        # ── CONFIRM SAFETY ─────────────────────────────────────────────────
        elif step == "confirm_safety":
            sug = session.get("p2_suggestions", {})
            hazards, ppe = _parse_safety_response(
                text, sug.get("hazards", []), sug.get("ppe", [])
            )
            fields["safety_compliance"] = {
                "site_hazards": hazards,
                "ppe_requirements": ppe,
            }
            session["p2_step"] = "review"
            return _build_summary(fields)

        # ── REVIEW & APPROVAL ──────────────────────────────────────────────
        elif step == "review":
            lower = text.strip().lower()

            if any(k in lower for k in APPROVAL_KEYWORDS):
                return None  # Signal: generate document

            if "change duties" in lower or "redo duties" in lower:
                session["p2_step"] = "suggest_duties"
                text = ""
                continue
            if "change safety" in lower or "redo safety" in lower or "change hazard" in lower or "change ppe" in lower:
                session["p2_step"] = "suggest_safety"
                text = ""
                continue

            # Check if user names a specific duty to re-do
            for i, duty in enumerate(fields.get("duties", [])):
                if duty["duty_name"].lower() in lower:
                    session["p2_step"] = f"suggest_tasks_{i}"
                    text = ""
                    break
            else:
                return (
                    _build_summary(fields) + "\n\n"
                    "Type **APPROVE** to generate the document.\n"
                    "Say **change duties** or **change safety** to edit, "
                    "or name a specific duty to update its tasks."
                )
            continue

        # ── FALLBACK ───────────────────────────────────────────────────────
        else:
            session["p2_step"] = "suggest_duties"
            text = ""
            continue


# ─────────────────────────────────────────────────────────────────────────────
# Main message handler
# ─────────────────────────────────────────────────────────────────────────────

async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    # Reset: wipe session and send Phase 1 opening question directly
    if msg["text"].strip().lower() in RESET_COMMANDS:
        state_mgr.save(user_id, {})
        await _send_reply(
            msg,
            "Starting a new JBS session.\n\nWhat is the **Customer Name**? (the organisation that hired Certis)",
        )
        return

    # Complete session guard
    if session.get("status") == "complete":
        await _send_reply(msg, "This JBS session is complete. Send 'New JBS' to start a new session.")
        return

    current_phase = session.get("phase", 1)

    # ── PHASE 1 ────────────────────────────────────────────────────────────
    if current_phase == 1:
        response = _phase1_respond(msg["text"], session)
        if response is not None:
            state_mgr.save(user_id, session)
            await _send_reply(msg, response)
            return
        # Phase 1 confirmed — session["phase"] = 2; kick off Phase 2 immediately
        p2_input = ""
    else:
        p2_input = msg["text"]

    # ── PHASE 2 ────────────────────────────────────────────────────────────
    response = await _phase2_respond(p2_input, session)

    if response is None:
        # User approved — generate document
        try:
            jbs_json = PhaseController(session).build_jbs_json(session)
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{DOCUMENT_GENERATOR_URL}/generate",
                    json={"jbs_json": jbs_json},
                    timeout=60,
                )
                resp.raise_for_status()
                doc_url = resp.json()["download_url"]
            response = (
                "Your JBS document has been generated!\n\n"
                f"Download link (valid 15 minutes):\n{doc_url}"
            )
            session["status"] = "complete"
        except Exception:
            response = (
                "Document generation failed. Please type **APPROVE** to retry, "
                "or send 'New JBS' to start over."
            )

    state_mgr.save(user_id, session)
    await _send_reply(msg, response)


# ─────────────────────────────────────────────────────────────────────────────
# Teams reply helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _send_reply(msg: dict, text: str):
    if msg["channel"] == "teams":
        await _send_teams(
            service_url=msg["service_url"],
            conversation_id=msg["conversation_id"],
            reply_to_id=msg["reply_to"],
            text=text,
        )


async def _send_teams(service_url: str, conversation_id: str, reply_to_id: str, text: str):
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
