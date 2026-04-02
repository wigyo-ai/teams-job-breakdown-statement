"""
Phase Controller
Enforces the 2-phase JBS state machine.
  Phase 1 → Phase 2: triggered when site_category + phase1_confirmed are set.
  Phase 2: terminal phase — LLM manages internal progression (A→B→C).
           Document generation is triggered by approval keyword in orchestrator.
"""

from datetime import datetime

PHASE_REQUIRED_FIELDS = {
    # Phase 1 advances when site_category is known (binds the RAG collection) AND the
    # user has confirmed the Phase 1 summary (phase1_confirmed flag).
    1: ["site_category", "phase1_confirmed"],
    # Phase 2 is the full JBS interview (Sections A+B+C). The LLM manages internal
    # progression. The only code-level trigger is document generation (handled in
    # orchestrator when approval keyword detected). No programmatic advance needed.
    2: [],
}

SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "c0e682b7-e990-48de-9cdf-5f0f1bab73a1",
    "Aviation":    "1f29b343-6f58-45eb-98ed-f724e3dbe038",
    "Industrial":  "debcfa65-f032-4ada-99eb-b080b48ddec5",
    "Maritime":    "21a23bb0-2cfa-4561-8a19-ef408c03c980",
    "Retail":      "30ae7e9b-3812-4d96-ad34-357214210dcf",
}

# Exported so orchestrator can use them without duplicating definitions
APPROVAL_KEYWORDS = {"approved", "confirm", "yes", "proceed", "looks good", "approve"}


class PhaseController:
    def __init__(self, session: dict):
        self.session = session
        self.current_phase = session.get("phase", 1)
        self.fields = session.setdefault("collected_fields", {})

    def ingest_user_input(self, text: str):
        """No-op: Phase 1 data collection is now handled entirely in orchestrator._phase1_respond."""
        pass

    def advance_if_complete(self):
        """No-op: Phase advancement is handled in orchestrator._phase1_respond."""
        pass

    def is_approved(self, user_text: str) -> bool:
        return any(k in user_text.lower() for k in APPROVAL_KEYWORDS)

    def build_jbs_json(self, session: dict) -> dict:
        f = session.get("collected_fields", {})
        return {
            "jbs_version":  "1.0",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "metadata": {
                "customer_name": f.get("customer_name", ""),
                "site_name":     f.get("site_name", ""),
                "site_category": f.get("site_category", ""),
                "job_purpose":   f.get("job_purpose", ""),
                "created_by":    session.get("user_id", ""),
                "authorized_by": f.get("authorized_by", ""),
            },
            "duties":            f.get("duties", []),
            "safety_compliance": f.get("safety_compliance", {}),
        }
