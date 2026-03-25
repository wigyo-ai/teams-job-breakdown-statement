"""
Microsoft Graph API client for SharePoint document access.
Uses Azure AD client credentials (app registration) — no user login required.
"""

import os
import httpx
from azure.identity import ClientSecretCredential


class GraphAPIClient:
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self):
        self._credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        self._site_url = os.environ["SP_SITE_URL"]

    def _token(self) -> str:
        return self._credential.get_token("https://graph.microsoft.com/.default").token

    async def list_changed_documents(self, library_id: str) -> list[dict]:
        """List documents in a SharePoint library modified in the last 24 hours."""
        headers = {"Authorization": f"Bearer {self._token()}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.GRAPH_BASE}/drives/{library_id}/root/children"
                "?$select=id,name,lastModifiedDateTime"
                "&$filter=lastModifiedDateTime ge 1900-01-01",  # full sync; narrow in prod
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("value", [])

    async def download_document(self, item_id: str) -> bytes:
        """Download a SharePoint drive item by ID."""
        headers = {"Authorization": f"Bearer {self._token()}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.GRAPH_BASE}/drives/items/{item_id}/content",
                headers=headers,
                follow_redirects=True,
                timeout=60,
            )
            r.raise_for_status()
            return r.content
