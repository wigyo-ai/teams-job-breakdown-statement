"""
Conversation Orchestrator
Manages the 5-phase JBS interview state machine.
Delegates LLM inference + conversation history to h2oGPTe.
Phase state + collected fields stored in StateManager (memory/sqlite/external_redis).
"""

import os
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
    channel = msg["channel"]
    if channel == "whatsapp":
        await _send_whatsapp(msg["reply_to"], text)
    elif channel == "telegram":
        await _send_telegram(msg["reply_to"], text)


async def _send_whatsapp(phone_number_id_recipient: str, text: str):
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    phone_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v19.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": phone_number_id_recipient,
                "type": "text",
                "text": {"body": text}
            }
        )


async def _send_telegram(chat_id: str, text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
