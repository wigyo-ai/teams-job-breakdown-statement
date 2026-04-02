"""
Prompt Builder
Loads phase-specific system prompts from config/prompts/ and injects session context.
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
        phase_prompt = phase_prompt.replace(
            "{{site_category}}", fields.get("site_category", "")
        )

        # STATE header — explicit reminder of active phase and known fields.
        state_header = (
            f"\n\n--- SESSION STATE ---\n"
            f"ACTIVE PHASE: {self.phase}\n"
            f"Site Category: {fields.get('site_category', 'not yet set')}\n"
            f"--- END STATE ---\n"
        )

        # For Phase 2, include the last 2 turns from Phase 1 so the LLM knows
        # the confirmed Customer Name, Site Name, Site Category, and Job Purpose
        # without needing to ask for them again.
        prior_context = ""
        if self.phase == 2:
            phase1_turns = [t for t in self.session.get("turns", []) if t.get("phase") == 1]
            if phase1_turns:
                last_turns = phase1_turns[-2:]
                prior_context = "\n\nCONFIRMED IN PHASE 1 (do not ask for these again):\n"
                for t in last_turns:
                    prior_context += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        # Only inject conversation turns from the current phase.
        phase_turns = [t for t in self.session.get("turns", []) if t.get("phase") == self.phase]
        history = ""
        if phase_turns:
            history = "\n\nCONVERSATION SO FAR THIS PHASE:\n"
            for t in phase_turns:
                history += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        return f"{base}\n\n{phase_prompt}{state_header}{prior_context}{history}"
