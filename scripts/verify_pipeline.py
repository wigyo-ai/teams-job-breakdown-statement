"""
End-to-end verification script for the SharePoint → Document AI → H2O.ai GPTe RAG pipeline.

Usage:
    export $(cat config/.env | xargs)
    python scripts/verify_pipeline.py

Checks:
  1. SharePoint / Graph API authentication
  2. SharePoint library accessibility + document counts
  3. H2O.ai GPTe connectivity
  4. Collection existence & ID alignment (hardcoded UUIDs vs live h2oGPTe)
  5. Test RAG probe query per collection
  6. Sync delta-filter warning
"""

import asyncio
import os
import sys

# Allow running from repo root without installing the package
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Auto-load config/.env if present
_env_path = os.path.join(REPO_ROOT, "config", ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=False)

from src.agent.phase_controller import SITE_CATEGORY_COLLECTION_MAP
from src.integrations.graph_api_client import GraphAPIClient
from src.rag.sharepoint_sync import SITE_CATEGORY_LIBRARY_MAP
from src.rag.h2ogpte_client import H2OGPTeClient

PASS = "\u2713"
FAIL = "\u2717"
WARN = "\u26a0"


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


async def check_sharepoint_auth(graph: GraphAPIClient) -> bool:
    section("Check 1 — SharePoint / Graph API Authentication")
    try:
        token = graph._token()
        if token:
            print(f"  {PASS}  Azure AD token obtained successfully")
            return True
        print(f"  {FAIL}  Token fetch returned empty string")
        return False
    except Exception as e:
        print(f"  {FAIL}  Auth failed: {e}")
        return False


async def check_sharepoint_libraries(graph: GraphAPIClient) -> dict[str, int]:
    section("Check 2 — SharePoint Library Accessibility")
    counts: dict[str, int] = {}
    for category, library_id in SITE_CATEGORY_LIBRARY_MAP.items():
        if not library_id:
            print(f"  {WARN}  {category}: SP_LIBRARY env var not set — skipping")
            continue
        try:
            docs = await graph.list_changed_documents(library_id)
            counts[category] = len(docs)
            print(f"  {PASS}  {category}: {len(docs)} document(s) found  (library={library_id[:8]}…)")
        except Exception as e:
            counts[category] = -1
            print(f"  {FAIL}  {category}: {e}")
    return counts


def check_h2ogpte_connection(h2o: H2OGPTeClient) -> list | None:
    section("Check 3 — H2O.ai GPTe Connectivity")
    try:
        collections = h2o.client.list_recent_collections(0, 100)
        print(f"  {PASS}  Connected — {len(collections)} collection(s) visible")
        return collections
    except Exception as e:
        print(f"  {FAIL}  Connection failed: {e}")
        return None


def check_collection_alignment(h2o: H2OGPTeClient, live_collections: list) -> dict[str, str | None]:
    section("Check 4 — Collection Existence & ID Alignment")
    live_by_name = {c.name: c for c in live_collections}
    live_by_id   = {c.id:   c for c in live_collections}
    resolved: dict[str, str | None] = {}

    for category, expected_id in SITE_CATEGORY_COLLECTION_MAP.items():
        expected_name = f"collection_{category.lower()}"

        # Check by UUID first, then by name
        live_col = live_by_id.get(expected_id) or live_by_name.get(expected_name)

        if live_col is None:
            print(f"  {FAIL}  {category}: collection not found "
                  f"(expected id={expected_id[:8]}… name={expected_name})")
            resolved[category] = None
            continue

        id_match = live_col.id == expected_id
        id_icon  = PASS if id_match else WARN

        try:
            doc_count = len(h2o.client.list_documents_in_collection(live_col.id, 0, 200))
        except Exception:
            doc_count = -1

        doc_icon = PASS if doc_count > 0 else WARN
        print(
            f"  {id_icon}  {category}: id={'matches' if id_match else 'MISMATCH — live=' + live_col.id[:8] + '…'}  "
            f"{doc_icon} {doc_count} doc(s)"
        )
        if not id_match:
            print(f"       expected={expected_id}")
            print(f"       live    ={live_col.id}")

        resolved[category] = live_col.id

    return resolved


async def check_rag_queries(h2o: H2OGPTeClient, resolved_ids: dict[str, str | None]):
    section("Check 5 — Test RAG Probe Query per Collection")
    probe = "List one typical duty for this site type."
    system_prompt = (
        "You are a JBS assistant. Use the knowledge base. "
        "Output ONLY a single duty name — no preamble."
    )
    for category, collection_id in resolved_ids.items():
        if not collection_id:
            print(f"  {WARN}  {category}: skipped (no valid collection_id)")
            continue
        try:
            reply, _ = await h2o.chat(
                collection_id=collection_id,
                conversation_id=None,
                message=probe,
                system_prompt=system_prompt,
            )
            snippet = reply.strip()[:80].replace("\n", " ")
            icon = PASS if snippet else WARN
            print(f"  {icon}  {category}: \"{snippet}\"")
        except Exception as e:
            print(f"  {FAIL}  {category}: RAG query failed — {e}")


def check_sync_delta_filter():
    section("Check 6 — Sync Delta-Filter (known issue)")
    sentinel = "1900-01-01"
    graph_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "integrations", "graph_api_client.py"
    )
    try:
        with open(graph_file) as f:
            content = f.read()
        if sentinel in content:
            print(f"  {WARN}  graph_api_client.py uses date filter '{sentinel}'")
            print(f"       This causes a full re-sync every run instead of 24-hour delta.")
            print(f"       Fix: replace sentinel with `(datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')`")
        else:
            print(f"  {PASS}  Delta-filter looks correct (no '{sentinel}' sentinel found)")
    except Exception as e:
        print(f"  {WARN}  Could not read graph_api_client.py: {e}")


async def main():
    print("\nJBS — Pipeline Verification")
    print("====================================")

    graph = GraphAPIClient()
    h2o   = H2OGPTeClient()

    auth_ok = await check_sharepoint_auth(graph)
    if auth_ok:
        await check_sharepoint_libraries(graph)

    live_collections = check_h2ogpte_connection(h2o)
    if live_collections is not None:
        resolved = check_collection_alignment(h2o, live_collections)
        await check_rag_queries(h2o, resolved)

    check_sync_delta_filter()

    print("\n" + "=" * 60)
    print("  Verification complete.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
