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
        """
        List all documents in a SharePoint drive library.

        Uses the search endpoint to recursively find all files across folders.
        The Graph API drive search returns only file items (not folders) and
        works with the b!... drive ID format required by the Graph API.
        """
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        headers = {"Authorization": f"Bearer {self._token()}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.GRAPH_BASE}/drives/{library_id}/root/search(q='')"
                "?$select=id,name,lastModifiedDateTime",
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            all_items = r.json().get("value", [])
            # Return only files (not folders) modified in last 24 h
            return [
                item for item in all_items
                if not item.get("folder")
                and item.get("lastModifiedDateTime", "") >= since
            ]

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
