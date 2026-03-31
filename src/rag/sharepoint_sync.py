"""
SharePoint → h2oGPTe sync pipeline.
Pulls documents changed in last 24h and ingests them into per-category collections.
Runs as an Azure Container Apps Scheduled Job (daily 02:00 UTC) or on-demand via admin dashboard.
"""

import os
import tempfile
from .h2ogpte_client import H2OGPTeClient
from ..integrations.graph_api_client import GraphAPIClient

SITE_CATEGORY_LIBRARY_MAP = {
    "Corporate":  os.environ.get("SP_LIBRARY_CORPORATE"),
    "Aviation":   os.environ.get("SP_LIBRARY_AVIATION"),
    "Industrial": os.environ.get("SP_LIBRARY_INDUSTRIAL"),
    "Maritime":   os.environ.get("SP_LIBRARY_MARITIME"),
    "Retail":     os.environ.get("SP_LIBRARY_RETAIL"),
}


async def sync_sharepoint_to_h2ogpte():
    graph   = GraphAPIClient()
    h2ogpte = H2OGPTeClient()

    for category, library_id in SITE_CATEGORY_LIBRARY_MAP.items():
        if not library_id:
            print(f"[sync] Skipping {category} — SP_LIBRARY env var not set")
            continue

        collection_id = h2ogpte.get_or_create_collection(
            name=f"collection_{category.lower()}",
            description=f"SOPs and historical JBS documents for {category} sites",
        )

        docs = await graph.list_changed_documents(library_id)
        print(f"[sync] {category}: {len(docs)} changed document(s)")

        for doc in docs:
            with tempfile.NamedTemporaryFile(suffix=f"_{doc['name']}", delete=False) as tmp:
                content = await graph.download_document(doc["id"])
                tmp.write(content)
                tmp_path = tmp.name

            h2ogpte.ingest_document(collection_id, tmp_path)
            print(f"[sync]   ✓ {doc['name']} → {category} collection")


if __name__ == "__main__":
    import asyncio
    asyncio.run(sync_sharepoint_to_h2ogpte())
