"""
H2O Enterprise h2oGPTe API client.

h2oGPTe manages ALL conversation history internally via conversation_id.
We do not maintain a separate turn history — this eliminates the Redis dependency.
"""

import os
import h2ogpte


class _H2OGPTEClient(h2ogpte.H2OGPTE):
    """Subclass that skips the version check, which fails on some server builds."""
    def _check_version(self, strict_version_check):
        pass


class H2OGPTeClient:
    def __init__(self):
        self.client = _H2OGPTEClient(
            address=os.environ["H2OGPTE_ADDRESS"],
            api_key=os.environ["H2OGPTE_API_KEY"],
        )

    async def chat(
        self,
        collection_id: str | None,
        conversation_id: str | None,
        message: str,
        system_prompt: str,
    ) -> tuple[str, str]:
        """
        Send a message to h2oGPTe.
        Returns (reply_text, conversation_id).

        If conversation_id is None, h2oGPTe creates a new conversation
        and we store the returned ID in the session for subsequent turns.
        h2oGPTe natively maintains the full turn history — no Redis needed.
        """
        if not conversation_id:
            # create_chat_session(collection_id) -> str (session ID)
            conversation_id = self.client.create_chat_session(
                collection_id=collection_id,
            )

        reply = self.client.answer_question(
            question=message,
            system_prompt=system_prompt,
            timeout=60,
        )
        return reply.content, conversation_id

    def get_or_create_collection(self, name: str, description: str) -> str:
        collections = self.client.list_recent_collections(0, 100)
        for c in collections:
            if c.name == name:
                return c.id
        col = self.client.create_collection(name=name, description=description)
        return col.id

    def ingest_document(self, collection_id: str, file_path: str):
        with open(file_path, "rb") as f:
            upload = self.client.upload(
                file_name=os.path.basename(file_path),
                file=f,
            )
        self.client.ingest_uploads(collection_id, [upload.id])
