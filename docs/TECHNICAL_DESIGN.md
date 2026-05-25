# Technical Design — JBS Automation Platform

## 1. Repository Structure

```
jbs-platform/
├── README.md
├── docs/
│   ├── SOLUTION_ARCHITECTURE.md
│   ├── TECHNICAL_DESIGN.md
│   ├── DEPLOYMENT_GUIDE.md
│   ├── CONFIGURATION_REFERENCE.md
│   ├── deployment-guide.html       # Interactive HTML deployment guide
│   └── solution-diagram.html       # Interactive architecture diagram
├── src/
│   ├── webhook/                    # Webhook receiver service (FastAPI, port 8000)
│   │   ├── main.py
│   │   ├── teams.py                # Microsoft Teams Bot Framework event parser
│   │   └── schema.py
│   ├── agent/                      # Conversation orchestrator (FastAPI, port 8001)
│   │   ├── server.py               # FastAPI entrypoint (/health, /process)
│   │   ├── orchestrator.py         # 2-phase hybrid state machine + Teams reply sender
│   │   ├── phase_controller.py     # Simplified stub (ingest_user_input/advance_if_complete are no-ops)
│   │   ├── prompt_builder.py       # Assembles system prompt from phase prompt files
│   │   └── state_manager.py        # Session persistence (memory / sqlite / external_redis)
│   ├── rag/
│   │   ├── h2ogpte_client.py       # h2oGPTe API wrapper (LLM + RAG)
│   │   └── sharepoint_sync.py      # SharePoint → h2oGPTe ingestion (ACA scheduled job)
│   ├── integrations/
│   │   └── graph_api_client.py     # Microsoft Graph API client (SharePoint access)
│   └── document/                   # Document generator service (FastAPI, port 8002)
│       ├── server.py               # FastAPI entrypoint (/health, /generate)
│       └── generator.py            # python-docx renderer + Azure Blob Storage upload
├── config/
│   └── prompts/
│       ├── system_base.txt         # STRICT OUTPUT RULES (no simulated responses, one question at a time)
│       ├── phase1.txt              # Documents the 4-field collection sequence (now code-driven)
│       └── phase2.txt              # Reference for Section A (Duties), B (Safety), C (Review)
├── templates/
│   └── jbs_corporate_template.docx # Word template with {BOOKMARK} placeholders
├── deploy/
│   ├── azure/
│   │   ├── main.bicep              # Bicep IaC — provisions full ACA environment
│   │   └── README.md               # Bicep deployment instructions
│   └── helm/
│       ├── Chart.yaml              # Reference Helm chart (not primary deployment)
│       └── values.yaml             # Reference env-var mapping for ACA parameters
├── .github/
│   └── workflows/
│       └── deploy.yml              # CI/CD: build → push to ACR → az containerapp update
├── docker-compose.yml              # Local development environment (3 services)
├── Dockerfile.webhook
├── Dockerfile.orchestrator
├── Dockerfile.document
├── Dockerfile.dashboard            # Built locally; deployed via HAIC App Store
└── requirements.txt
```

---

## 2. Webhook Service

**File:** `src/webhook/main.py`

```python
import os, jwt
from jwt import PyJWKClient
from fastapi import FastAPI, Request, HTTPException
from .schema import NormalisedMessage
from .teams import parse_teams_event
import httpx

app = FastAPI(title="JBS Webhook Service", version="2.0.0")
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]
TEAMS_APP_ID     = os.environ["TEAMS_APP_ID"]

# Bot Framework publishes RS256 signing keys at this well-known JWKS endpoint
_jwks_client = PyJWKClient(
    "https://login.botframework.com/v1/.well-known/keys",
    cache_keys=True,
)

@app.post("/webhook/teams")
async def teams_webhook(request: Request):
    """Receive a Bot Framework Activity from Microsoft Teams."""
    auth_header = request.headers.get("Authorization", "")
    _verify_teams_token(auth_header)
    payload = await request.json()
    msg = parse_teams_event(payload)
    if msg:
        await _forward(msg)
    return {}

def _verify_teams_token(auth_header: str):
    """Validate the RS256 JWT Bearer token issued by Azure Bot Service."""
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header[7:]
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        jwt.decode(token, signing_key.key, algorithms=["RS256"],
                   audience=TEAMS_APP_ID, issuer="https://api.botframework.com")
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

async def _forward(msg: NormalisedMessage):
    async with httpx.AsyncClient() as client:
        await client.post(f"{ORCHESTRATOR_URL}/process", json=msg.dict(), timeout=30)
```

**File:** `src/webhook/teams.py`

```python
from .schema import NormalisedMessage
from datetime import datetime, timezone

def parse_teams_event(payload: dict) -> NormalisedMessage | None:
    """Parse a Bot Framework Activity sent by Microsoft Teams."""
    try:
        if payload.get("type") != "message":
            return None   # ignore typing indicators, reactions, events

        text = (payload.get("text") or "").strip()
        if not text:
            return None

        from_user    = payload.get("from", {})
        conversation = payload.get("conversation", {})
        timestamp    = payload.get("timestamp") or datetime.now(tz=timezone.utc).isoformat()

        # Prefer stable AAD object ID; fall back to Teams channel user ID
        user_id = from_user.get("aadObjectId") or from_user.get("id", "")

        return NormalisedMessage(
            channel="teams",
            user_id=user_id,
            user_name=from_user.get("name"),
            text=text,
            timestamp=timestamp,
            reply_to=payload.get("id", ""),          # incoming activity ID
            service_url=payload.get("serviceUrl", ""),
            conversation_id=conversation.get("id", ""),
        )
    except (KeyError, TypeError):
        return None
```

**File:** `src/webhook/schema.py`

```python
from pydantic import BaseModel
from typing import Optional

class NormalisedMessage(BaseModel):
    channel:         str            # "teams"
    user_id:         str            # AAD object ID (stable user identifier)
    user_name:       Optional[str] = None
    text:            str
    timestamp:       str            # ISO-8601
    reply_to:        str            # Teams: incoming activity ID (used in reply URL)
    service_url:     Optional[str] = None   # Teams: Bot Framework service URL
    conversation_id: Optional[str] = None   # Teams: conversation ID
```

---

## 3. Conversation Orchestrator

**File:** `src/agent/orchestrator.py`

The orchestrator implements a **2-phase hybrid architecture**. The code drives the entire state machine; h2oGPTe is called for suggestions only, not to determine conversation flow.

### Phase 1 — Setup (code-driven, no LLM)

A hardcoded 4-step counter collects fields in sequence:

1. Customer Name
2. Site Name
3. Site Category (one of: Corporate, Aviation, Industrial, Maritime, Retail)
4. Job Purpose

No h2oGPTe calls are made during Phase 1. The site category is used to select the appropriate h2oGPTe RAG collection for Phase 2.

### Phase 2 — Interview (hybrid LLM+RAG)

The code drives a `p2_step` state machine through these steps in order:

```
suggest_duties → confirm_duties
→ suggest_tasks_0 → confirm_tasks_0 → ... → suggest_tasks_N → confirm_tasks_N
→ suggest_safety → confirm_safety
→ review → APPROVE → generate document
```

At each `suggest_*` step, h2oGPTe is called **once** with `conversation_id=None` (a fresh RAG query; no accumulated conversation state across sections). The user confirms or modifies the suggestion; confirmed data is stored directly in `collected_fields`. On APPROVE, the orchestrator POSTs the collected JSON to the Document Generator service.

### Key behaviours

- **Message deduplication:** Each incoming activity ID is tracked; duplicate deliveries from Teams are dropped.
- **Stale message filtering:** Reset commands (`new jbs`, `start over`, etc.) older than 10 seconds are dropped on active sessions to prevent Teams re-delivery from wiping in-progress interviews.
- **Bot Framework replies:** The orchestrator sends replies via the Bot Framework REST API using a cached OAuth2 client-credentials token (Bot Framework scope).

### Bot token flow

```
POST https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token
  grant_type=client_credentials
  client_id=$TEAMS_APP_ID
  client_secret=$TEAMS_APP_PASSWORD
  scope=https://api.botframework.com/.default

Reply URL: {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}
```

---

## 4. State Manager

**File:** `src/agent/state_manager.py`

The `StateManager` selects a backend via the `STATE_BACKEND` environment variable:

| `STATE_BACKEND` value | Backend | Notes |
|---|---|---|
| `memory` | `MemoryStateBackend` | **Current production setting.** Process-local dict. Requires `--workers 1`. Sessions lost on container restart. |
| `sqlite` | `SQLiteStateBackend` | Restart-safe for a single replica. **Not used in production** — SQLite on Azure Files (SMB mount) fails due to POSIX advisory lock incompatibility. |
| `external_redis` | `ExternalRedisStateBackend` | Multi-replica safe. Requires an Azure Cache for Redis instance (additional infrastructure). |

> **Single-worker requirement:** The memory backend stores sessions in a process-local dict. Running multiple uvicorn workers (`--workers > 1`) causes split-brain — concurrent requests for the same user may land on different workers with different session snapshots. The Dockerfile sets `--workers 1` and this must not be changed while `STATE_BACKEND=memory` is active.

> **deepcopy fix:** `MemoryStateBackend.load()` returns a `copy.deepcopy` of the stored session dict. This prevents shared-reference bugs where in-flight async handlers mutate the same object concurrently before `save()` is called.

> **Known limitation:** Container restarts (e.g. during rolling deployments) wipe all active in-memory sessions. Users need to type "New JBS" to restart. A persistent backend (Azure Cache for Redis or Azure Blob) would solve this but requires new infrastructure.

---

## 5. Phase Controller

**File:** `src/agent/phase_controller.py`

In the current architecture, `phase_controller.py` is a simplified stub. The orchestrator (`orchestrator.py`) handles all phase and step transitions directly using session state. The `PhaseController` class is retained for backwards compatibility but its two key methods are effectively no-ops:

- `ingest_user_input(text)` — no longer extracts fields from free text; the orchestrator handles field collection via its own step logic.
- `advance_if_complete()` — no longer drives phase transitions; the orchestrator manages the `p2_step` state machine directly.

The `SITE_CATEGORY_COLLECTION_MAP` (mapping site category strings to h2oGPTe collection IDs) is still defined here and referenced by the orchestrator when selecting the RAG collection for Phase 2.

The `build_jbs_json(session)` method is still used by the orchestrator to assemble the final payload sent to the Document Generator on approval. It reads from `session["collected_fields"]` which the orchestrator populates directly at each `confirm_*` step.

---

## 6. h2oGPTe API Client

**File:** `src/rag/h2ogpte_client.py`

```python
import os
import h2ogpte  # pip install h2ogpte

class H2OGPTeClient:
    def __init__(self):
        self.client = h2ogpte.H2OGPTE(
            address=os.environ["H2OGPTE_ADDRESS"],
            api_key=os.environ["H2OGPTE_API_KEY"]
        )

    async def chat(self, collection_id, conversation_id, message, system_prompt):
        if not conversation_id:
            conv = self.client.create_conversation(
                collection_id=collection_id,
                system_prompt=system_prompt
            )
            conversation_id = conv.id

        reply = self.client.answer_question(
            conversation_id=conversation_id,
            messages=[h2ogpte.Message(role="user", content=message)]
        )
        return reply.content, conversation_id

    def get_or_create_collection(self, name: str, description: str) -> str:
        collections = self.client.list_recent_collections(0, 100)
        for c in collections:
            if c.name == name:
                return c.id
        return self.client.create_collection(name=name, description=description).id

    def ingest_document(self, collection_id: str, file_path: str):
        with open(file_path, "rb") as f:
            upload = self.client.upload(file_name=os.path.basename(file_path), file=f)
        self.client.ingest_uploads(collection_id, [upload.id])
```

---

## 7. SharePoint Sync Pipeline

**File:** `src/rag/sharepoint_sync.py`

```python
import os, tempfile
from .h2ogpte_client import H2OGPTeClient
from ..integrations.graph_api_client import GraphAPIClient

SITE_CATEGORY_LIBRARY_MAP = {
    "Corporate":   os.environ.get("SP_LIBRARY_CORPORATE"),
    "Aviation":    os.environ.get("SP_LIBRARY_AVIATION"),
    "Industrial":  os.environ.get("SP_LIBRARY_INDUSTRIAL"),
    "Maritime":    os.environ.get("SP_LIBRARY_MARITIME"),
    "Retail":      os.environ.get("SP_LIBRARY_RETAIL"),
}

async def sync_sharepoint_to_h2ogpte():
    graph   = GraphAPIClient()
    h2ogpte = H2OGPTeClient()

    for category, library_id in SITE_CATEGORY_LIBRARY_MAP.items():
        if not library_id:
            continue
        collection_id = h2ogpte.get_or_create_collection(
            name=f"collection_{category.lower()}",
            description=f"SOPs and JBS documents for {category} sites"
        )
        for doc in await graph.list_changed_documents(library_id):
            with tempfile.NamedTemporaryFile(suffix=doc["name"], delete=False) as tmp:
                tmp.write(await graph.download_document(doc["id"]))
                tmp_path = tmp.name
            h2ogpte.ingest_document(collection_id, tmp_path)
            print(f"Synced: {doc['name']} → {category} collection")
```

---

## 8. Document Generator

**File:** `src/document/generator.py`

```python
import os, json, uuid
from datetime import datetime, timezone, timedelta
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from docx import Document
from docx.shared import Pt, RGBColor

TEMPLATE_PATH   = os.path.join(os.path.dirname(__file__), "../../templates/jbs_corporate_template.docx")
AZURE_ACCOUNT   = os.environ.get("AZURE_STORAGE_ACCOUNT", "")
AZURE_CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER", "jbs-documents")
BLOB_PREFIX     = os.environ.get("BLOB_PREFIX", "jbs-documents/")
URL_EXPIRY      = int(os.environ.get("DOC_URL_EXPIRY_SECONDS", "900"))

class DocumentGenerator:
    def __init__(self):
        account_key = os.environ["AZURE_STORAGE_KEY"]
        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={AZURE_ACCOUNT};"
            f"AccountKey={account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        self.blob_service = BlobServiceClient.from_connection_string(conn_str)
        self.account_key  = account_key

    async def generate(self, jbs_json: dict) -> str:
        doc = Document(TEMPLATE_PATH)
        self._populate_document(doc, jbs_json)
        site       = jbs_json["metadata"]["site_name"].replace(" ", "_")
        filename   = f"JBS_{site}_{uuid.uuid4().hex[:8]}.docx"
        local_path = f"/tmp/{filename}"
        doc.save(local_path)
        blob_name  = f"{BLOB_PREFIX}{filename}"
        container_client = self.blob_service.get_container_client(AZURE_CONTAINER)
        with open(local_path, "rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
        expiry    = datetime.now(timezone.utc) + timedelta(seconds=URL_EXPIRY)
        sas_token = generate_blob_sas(
            account_name=AZURE_ACCOUNT,
            container_name=AZURE_CONTAINER,
            blob_name=blob_name,
            account_key=self.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        return f"https://{AZURE_ACCOUNT}.blob.core.windows.net/{AZURE_CONTAINER}/{blob_name}?{sas_token}"
```

---

## 9. H2O Wave Admin Dashboard

The admin dashboard is a H2O Wave application deployed via the **HAIC App Store** — it is NOT deployed as an Azure Container App. Build the dashboard image using `Dockerfile.dashboard` and deploy via `h2o bundle deploy` through the HAIC console (see Step 12c in `docs/DEPLOYMENT_GUIDE.md`).

The dashboard connects to the Orchestrator service via `ORCHESTRATOR_URL` (internal ACA ingress) to:
- List active JBS interview sessions and their current phase (`GET /sessions`)
- Trigger on-demand SharePoint sync (`POST /admin/sync`)
- Display generated document metadata from Azure Blob Storage

**Key environment variables for the dashboard:**

| Variable | Description |
|---|---|
| `ORCHESTRATOR_URL` | Internal ACA URL of the orchestrator service |
| `H2OGPTE_ADDRESS` | h2oGPTe instance URL (for metrics display) |

---

## 10. Prompt Templates

There are three active prompt files. The old `phase3.txt` and `phase4.txt` have been removed — the 4-phase LLM-driven structure no longer exists.

**File:** `config/prompts/system_base.txt`

Contains STRICT OUTPUT RULES applied globally:
- No simulated user responses
- Ask only one question at a time
- No invented facts — ground all content in the retrieved RAG context
- Keep responses concise for the Teams messaging interface

**File:** `config/prompts/phase1.txt`

Documents the 4-field collection sequence (Customer Name, Site Name, Site Category, Job Purpose). This file is largely a reference — Phase 1 is now entirely code-driven with no LLM calls. The orchestrator presents each field prompt directly from code.

**File:** `config/prompts/phase2.txt`

Reference prompt describing the three sections the orchestrator works through in Phase 2:
- **Section A — Duties:** h2oGPTe suggests standard duties for the site category; user confirms or edits.
- **Section B — Safety:** h2oGPTe suggests safety requirements (hazards, PPE, escalation); user confirms or edits.
- **Section C — Review:** Orchestrator presents a structured summary of all confirmed data for final approval.

The actual conversation flow is code-driven (`p2_step` state machine in `orchestrator.py`). These prompt files serve as content guidance for the h2oGPTe suggestion calls — they are not used to drive turn-by-turn conversation logic.


---

## 11. Environment Variables Reference

| Variable | Service | Description |
|---|---|---|
| `H2OGPTE_ADDRESS` | Orchestrator | h2oGPTe server URL |
| `H2OGPTE_API_KEY` | Orchestrator | h2oGPTe API key (from Azure Key Vault) |
| `STATE_BACKEND` | Orchestrator | `memory` \| `sqlite` \| `external_redis` — **production: `memory`** |
| `SQLITE_PATH` | Orchestrator | SQLite file path (set but unused in production; memory backend is active) |
| `SESSION_TTL_HOURS` | Orchestrator | Session time-to-live in hours (default: 24) |
| `TEAMS_APP_ID` | Webhook + Orchestrator | Azure Bot App Registration client ID |
| `TEAMS_APP_PASSWORD` | Orchestrator | Azure Bot App Registration client secret |
| `AZURE_TENANT_ID` | Sync pipeline | Azure AD tenant ID for SharePoint auth |
| `AZURE_CLIENT_ID` | Sync pipeline | Azure AD app client ID |
| `AZURE_CLIENT_SECRET` | Sync pipeline | Azure AD app client secret |
| `SP_SITE_URL` | Sync pipeline | SharePoint site URL |
| `SP_LIBRARY_CORPORATE` | Sync pipeline | SharePoint library ID for Corporate SOPs |
| `SP_LIBRARY_AVIATION` | Sync pipeline | SharePoint library ID for Aviation SOPs |
| `SP_LIBRARY_INDUSTRIAL` | Sync pipeline | SharePoint library ID for Industrial SOPs |
| `SP_LIBRARY_MARITIME` | Sync pipeline | SharePoint library ID for Maritime SOPs |
| `SP_LIBRARY_RETAIL` | Sync pipeline | SharePoint library ID for Retail SOPs |
| `AZURE_STORAGE_ACCOUNT` | Document Gen | Azure Storage account name |
| `AZURE_STORAGE_CONTAINER` | Document Gen | Blob container name (default: `jbs-documents`) |
| `AZURE_STORAGE_KEY` | Document Gen | **Required.** Storage account access key — must be set or document generation fails. Store in Azure Key Vault. |
| `BLOB_PREFIX` | Document Gen | Blob name prefix for stored documents (default: `jbs-documents/`) |
| `DOC_URL_EXPIRY_SECONDS` | Document Gen | SAS URL validity in seconds (default: `900` = 15 min) |
| `ORCHESTRATOR_URL` | Webhook, Dashboard | Internal URL of orchestrator service |
| `TENANT_ID` | Orchestrator | tenant identifier (value: `jbs`) |
| `AZURE_TENANT_ID` | Orchestrator | Azure AD tenant ID (`35013e61-d285-4f21-9b33-4c601cc1d8ce`) |
