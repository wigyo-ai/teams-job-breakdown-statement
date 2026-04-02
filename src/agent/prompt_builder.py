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

        # Inject site_category into phase prompt
        fields = self.session.get("collected_fields", {})
        phase_prompt = phase_prompt.replace(
            "{{site_category}}", fields.get("site_category", "")
        )

        # STATE header — explicit reminder of active phase and known fields.
        # The LLM must not contradict these values.
        state_lines = [
            f"ACTIVE PHASE: {self.phase}",
            f"Site Category: {fields.get('site_category', 'not yet set')}",
        ]
        state_header = "\n\n--- SESSION STATE ---\n" + "\n".join(state_lines) + "\n--- END STATE ---\n"

        all_turns = self.session.get("turns", [])

        # For Phase 2+, include the last 2 turns from the PREVIOUS phase so the
        # LLM can see exactly what was confirmed (e.g. the Phase 1 summary and
        # the user's confirmation).  Without this, Phase 2 has no knowledge of
        # Customer Name, Site Name, or Job Purpose, causing it to ask again.
        prior_context = ""
        if self.phase > 1:
            prev_turns = [t for t in all_turns if t.get("phase") == self.phase - 1]
            if prev_turns:
                last_prev = prev_turns[-2:]  # summary + confirmation turns
                prior_context = f"\n\nCONFIRMED IN PHASE {self.phase - 1} (do not ask for these again):\n"
                for t in last_prev:
                    prior_context += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        # Only inject conversation turns from the CURRENT phase.
        # Cross-phase history causes the LLM to revert to earlier-phase behaviour.
        phase_turns = [t for t in all_turns if t.get("phase") == self.phase]
        history = ""
        if phase_turns:
            history = "\n\nCONVERSATION SO FAR THIS PHASE:\n"
            for t in phase_turns:
                history += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        return f"{base}\n\n{phase_prompt}{state_header}{prior_context}{history}"
