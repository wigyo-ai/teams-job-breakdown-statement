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

        # State header — always reminds the LLM what phase it is in.
        # This is the single source of truth; the LLM must not contradict it.
        state_header = (
            f"\n\n--- SESSION STATE ---\n"
            f"ACTIVE PHASE: {self.phase}\n"
            f"Site Category: {fields.get('site_category', 'not yet set')}\n"
            f"--- END STATE ---\n"
        )

        # Only inject conversation turns from the CURRENT phase.
        # Cross-phase history causes the LLM to revert to earlier phase behaviour.
        # Each phase prompt's CONTEXT block already summarises what prior phases collected.
        all_turns = self.session.get("turns", [])
        phase_turns = [t for t in all_turns if t.get("phase") == self.phase]

        history = ""
        if phase_turns:
            history = "\n\nCONVERSATION SO FAR THIS PHASE:\n"
            for t in phase_turns:
                history += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        return f"{base}\n\n{phase_prompt}{state_header}{history}"
