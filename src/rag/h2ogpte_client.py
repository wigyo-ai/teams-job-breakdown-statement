"""
H2O Enterprise h2oGPTe API client.

h2oGPTe manages ALL conversation history internally via conversation_id.
We do not maintain a separate turn history — this eliminates the Redis dependency.
"""

import os
import h2ogpte


class _H2OGPTEClient(h2ogpte.H2OGPTE):
    """
    Subclass that skips version checks, which fail on some server builds.
    Both _check_version (called at init) and get_meta (called inside
    answer_question) hit /rpc/meta — the server returns an empty body,
    causing JSONDecodeError.  We bypass both.
    """
    def _check_version(self, strict_version_check):
        pass

    def get_meta(self):
        try:
            return super().get_meta()
        except Exception:
            class _Meta:
                version = "1.4.0"
            return _Meta()


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

        When a collection_id is provided the query is routed through a chat
        session so h2oGPTe performs RAG against the collection (uses
        connect()+session.query(), the SDK-recommended path).  Without a
        collection_id, answer_question is used for a plain LLM call.
        """
        if collection_id and not conversation_id:
            conversation_id = self.client.create_chat_session(
                collection_id=collection_id,
            )

        if conversation_id:
            with self.client.connect(conversation_id) as session:
                reply = session.query(
                    message,
                    system_prompt=system_prompt,
                    timeout=120,
                )
            return reply.content, conversation_id

        # No collection bound yet — plain LLM call (Phase 1 only)
        reply = self.client.answer_question(
            question=message,
            system_prompt=system_prompt,
            timeout=120,
        )
        return reply.content, None

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
