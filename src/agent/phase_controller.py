"""
Phase Controller
Enforces the 4-phase JBS interview state machine.
Determines collection ID from site category on Phase 1.
"""

from datetime import datetime

PHASE_REQUIRED_FIELDS = {
    1: ["customer_name", "site_name", "site_category", "job_purpose"],
    2: ["duties"],
    3: ["hazards", "ppe_requirements", "escalation_procedure"],
    4: [],
}

SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "c0e682b7-e990-48de-9cdf-5f0f1bab73a1",
    "Aviation":    "1f29b343-6f58-45eb-98ed-f724e3dbe038",
    "Industrial":  "debcfa65-f032-4ada-99eb-b080b48ddec5",
    "Maritime":    "21a23bb0-2cfa-4561-8a19-ef408c03c980",
    "Retail":      "30ae7e9b-3812-4d96-ad34-357214210dcf",
}

APPROVAL_KEYWORDS = {"approved", "confirm", "yes", "proceed", "looks good", "approve"}


class PhaseController:
    def __init__(self, session: dict):
        self.session = session
        self.current_phase = session.get("phase", 1)
        self.fields = session.setdefault("collected_fields", {})

    def ingest_user_input(self, text: str):
        """Extract structured fields from user input where possible."""
        lower = text.lower()

        # Phase 1: detect site category to bind the correct h2oGPTe collection
        if self.current_phase == 1 and "site_category" not in self.fields:
            for category in SITE_CATEGORY_COLLECTION_MAP:
                if category.lower() in lower:
                    self.fields["site_category"] = category
                    self.session["collection_id"] = SITE_CATEGORY_COLLECTION_MAP[category]
                    break

    def advance_if_complete(self):
        required = PHASE_REQUIRED_FIELDS.get(self.current_phase, [])
        if all(f in self.fields for f in required):
            if self.current_phase < 4:
                self.current_phase += 1
                self.session["phase"] = self.current_phase

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
