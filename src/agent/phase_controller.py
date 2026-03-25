"""
Phase Controller
Enforces the 5-phase JBS interview state machine.
Determines collection ID from site category on Phase 1.
"""

from datetime import datetime
import re

PHASE_REQUIRED_FIELDS = {
    1: ["customer_name", "site_name", "site_category", "job_purpose"],
    2: ["duties"],
    3: ["hazards", "ppe_requirements", "escalation_procedure"],
    4: ["mozart_site_id"],
    5: [],
}

SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "col_REPLACE_CORPORATE",
    "Aviation":    "col_REPLACE_AVIATION",
    "Industrial":  "col_REPLACE_INDUSTRIAL",
    "Maritime":    "col_REPLACE_MARITIME",
    "Retail":      "col_REPLACE_RETAIL",
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

    def extract_mozart_site_id(self, text: str) -> str | None:
        """Simple extraction — looks for patterns like SITE-XXX-000."""
        match = re.search(r'\bSITE-[A-Z0-9]+-\d+\b', text)
        return match.group(0) if match else None

    def advance_if_complete(self):
        required = PHASE_REQUIRED_FIELDS.get(self.current_phase, [])
        if all(f in self.fields for f in required):
            if self.current_phase < 5:
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
            "mozart_references": session.get("mozart_references", {}),
        }
