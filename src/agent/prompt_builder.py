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

        # Inject session context into phase prompt
        fields = self.session.get("collected_fields", {})
        phase_prompt = phase_prompt.replace(
            "{{site_category}}", fields.get("site_category", "")
        )

        # Include conversation history so the LLM has full context each turn
        turns = self.session.get("turns", [])
        history = ""
        if turns:
            history = "\n\nCONVERSATION HISTORY (most recent first shown last):\n"
            for t in turns:
                history += f"User: {t['user']}\nAssistant: {t['assistant']}\n\n"

        return f"{base}\n\n{phase_prompt}{history}"
