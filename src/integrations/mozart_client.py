"""Mozart REST API client — fetches reference document metadata by site ID."""

import os
import httpx


class MozartClient:
    def __init__(self):
        self.base_url = os.environ["MOZART_API_BASE_URL"]
        self.api_key  = os.environ["MOZART_API_KEY"]
        self.timeout  = int(os.environ.get("MOZART_TIMEOUT_SECONDS", "15"))

    async def get_references(self, site_id: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/sites/{site_id}/documents",
                headers=headers,
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()

        return {
            "site_id": site_id,
            "reference_documents": [
                {
                    "doc_id":    d["id"],
                    "doc_title": d["title"],
                    "doc_type":  d.get("type", "SOP"),
                    "mozart_url": d.get("url", ""),
                }
                for d in data.get("documents", [])
            ],
        }
