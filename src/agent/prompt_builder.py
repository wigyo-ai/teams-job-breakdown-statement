"""
Prompt Builder
Loads phase-specific system prompts and injects session state.
Phase 1 is handled entirely in code (orchestrator.py) so this builder
is only ever called for Phase 2.
"""

import os

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "../../config/prompts")


def _load(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    with open(path, "r") as f:
        return f.read()


class PromptBuilder:
    def __init__(self, session: dict, phase: int):
        self.session = session
        self.phase = phase
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        base = _load("system_base.txt")
        phase_prompt = _load(f"phase{self.phase}.txt")

        fields = self.session.get("collected_fields", {})
        phase_prompt = phase_prompt.replace("{{site_category}}", fields.get("site_category", ""))

        # Confirmed Phase 1 data — read directly from collected_fields (stored by
        # code, not parsed from LLM output, so guaranteed to be accurate).
        confirmed = (
            "\n\n--- CONFIRMED PHASE 1 DATA (do not ask for these again) ---\n"
            f"Customer Name: {fields.get('customer_name', 'not set')}\n"
            f"Site Name:     {fields.get('site_name', 'not set')}\n"
            f"Site Category: {fields.get('site_category', 'not set')}\n"
            f"Job Purpose:   {fields.get('job_purpose', 'not set')}\n"
            "--- END PHASE 1 DATA ---\n"
        )

        # Only inject Phase 2 conversation turns (no cross-phase noise)
        turns = [t for t in self.session.get("turns", []) if t.get("phase") == 2]
        history = ""
        if turns:
            history = "\n\nCONVERSATION SO FAR:\n"
            for t in turns:
                history += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        return f"{base}\n\n{phase_prompt}{confirmed}{history}"
