"""
JBS Admin Dashboard — H2O Wave application.

Connects to the Orchestrator service (ORCHESTRATOR_URL) to:
  - List active JBS interview sessions and their current phase  (GET /sessions)
  - Trigger on-demand SharePoint → h2oGPTe sync               (POST /admin/sync)

Environment variables:
  ORCHESTRATOR_URL   Internal ACA URL of the orchestrator (default: http://localhost:8001)
  H2OGPTE_ADDRESS    h2oGPTe instance URL shown in the header metrics row
"""

import os
import httpx
from h2o_wave import main, app, Q, ui

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8001")
H2OGPTE_ADDRESS  = os.environ.get("H2OGPTE_ADDRESS", "")

PHASE_LABELS = {
    1: "Phase 1 — Context & Initiation",
    2: "Phase 2 — Duty Discovery",
    3: "Phase 3 — Safety & Compliance",
    4: "Phase 4 — Review & Approval",
}

STATUS_COLORS = {
    "active":   "$blue",
    "complete": "$green",
    "error":    "$red",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fetch_sessions() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ORCHESTRATOR_URL}/sessions")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return [{"_error": str(exc)}]


async def _trigger_sync() -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{ORCHESTRATOR_URL}/admin/sync")
            r.raise_for_status()
            return "SharePoint sync started successfully."
    except Exception as exc:
        return f"Sync failed: {exc}"


def _build_sessions_table(sessions: list[dict]) -> ui.Component:
    if not sessions:
        return ui.text("No active sessions.", name="no_sessions")

    if sessions and "_error" in sessions[0]:
        return ui.text(f"Could not reach orchestrator: {sessions[0]['_error']}", name="error_msg")

    rows = []
    for s in sessions:
        phase_num  = s.get("phase", "—")
        phase_label = PHASE_LABELS.get(phase_num, f"Phase {phase_num}")
        status     = s.get("status", "active")
        fields     = s.get("collected_fields", {})
        customer   = fields.get("customer_name", "—")
        site       = fields.get("site_name", "—")
        category   = fields.get("site_category", "—")

        rows.append(
            ui.table_row(
                name=s.get("user_id", "unknown"),
                cells=[
                    s.get("user_id", "—"),
                    customer,
                    site,
                    category,
                    phase_label,
                    status,
                ],
            )
        )

    return ui.table(
        name="sessions_table",
        columns=[
            ui.table_column(name="user_id",  label="User ID",      min_width="160px"),
            ui.table_column(name="customer", label="Customer",     min_width="160px"),
            ui.table_column(name="site",     label="Site",         min_width="160px"),
            ui.table_column(name="category", label="Category",     min_width="120px"),
            ui.table_column(name="phase",    label="Phase",        min_width="240px"),
            ui.table_column(name="status",   label="Status",       min_width="100px"),
        ],
        rows=rows,
        height="400px",
    )


# ---------------------------------------------------------------------------
# Main Wave handler
# ---------------------------------------------------------------------------

@app("/")
async def serve(q: Q):
    # ---- button events ------------------------------------------------
    sync_message = ""
    if q.args.refresh_btn:
        pass  # just re-render

    if q.args.sync_btn:
        sync_message = await _trigger_sync()

    # ---- fetch data ---------------------------------------------------
    sessions = await _fetch_sessions()
    active_count = sum(
        1 for s in sessions
        if "_error" not in s and s.get("status") == "active"
    )

    # ---- layout -------------------------------------------------------
    q.page["meta"] = ui.meta_card(
        box="",
        title="JBS Admin",
        theme="h2o-dark",
    )

    q.page["header"] = ui.header_card(
        box="1 1 12 1",
        title="JBS Admin Dashboard",
        subtitle="Job Breakdown Statement Platform — Operations Console",
        icon="Shield",
        icon_color="$blue",
    )

    q.page["stats"] = ui.form_card(
        box="1 2 12 2",
        items=[
            ui.stats(
                items=[
                    ui.stat(
                        label="Active Sessions",
                        value=str(active_count),
                        icon="People",
                        icon_color="$blue",
                    ),
                    ui.stat(
                        label="Orchestrator",
                        value=ORCHESTRATOR_URL.replace("http://", "").split(":")[0],
                        icon="Server",
                        icon_color="$green",
                    ),
                    ui.stat(
                        label="h2oGPTe",
                        value=H2OGPTE_ADDRESS.replace("https://", "").split("/")[0] or "not configured",
                        icon="Brain",
                        icon_color="$amber",
                    ),
                ],
                justify="start",
            ),
        ],
    )

    q.page["actions"] = ui.form_card(
        box="1 4 12 1",
        items=[
            ui.inline(items=[
                ui.button(name="refresh_btn", label="Refresh Sessions", primary=True, icon="Refresh"),
                ui.button(name="sync_btn",    label="Trigger SharePoint Sync", icon="Sync"),
                ui.text(sync_message) if sync_message else ui.text(""),
            ]),
        ],
    )

    q.page["sessions"] = ui.form_card(
        box="1 5 12 6",
        title="Active Interview Sessions",
        items=[_build_sessions_table(sessions)],
    )

    q.page["footer"] = ui.footer_card(
        box="1 11 12 1",
        caption="JBS Platform — Powered by H2O Wave & Azure Container Apps",
    )

    await q.page.save()
