# Technical Design — JBS Automation Platform

## 1. Repository Structure

```
certis-jbs-platform/
├── README.md
├── docs/
│   ├── SOLUTION_ARCHITECTURE.md
│   ├── TECHNICAL_DESIGN.md
│   ├── DEPLOYMENT_GUIDE.md
│   └── CONFIGURATION_REFERENCE.md
├── src/
│   ├── webhook/                    # Webhook receiver service (FastAPI)
│   │   ├── main.py
│   │   ├── teams.py                # Microsoft Teams Bot Framework event parser
│   │   └── schema.py
│   ├── agent/                      # Conversation orchestrator
│   │   ├── orchestrator.py
│   │   ├── phase_controller.py
│   │   ├── prompt_builder.py
│   │   ├── state_manager.py
│   │   └── phases/
│   │       ├── phase1_context.py
│   │       ├── phase2_duties.py
│   │       ├── phase3_safety.py
│   │       ├── phase4_mozart.py
│   │       └── phase5_review.py
│   ├── rag/
│   │   ├── h2ogpte_client.py       # h2oGPTe API wrapper
│   │   ├── collection_manager.py   # Manage per-category collections
│   │   └── sharepoint_sync.py      # SharePoint → h2oGPTe ingestion
│   ├── integrations/
│   │   ├── mozart_client.py        # Mozart REST API client
│   │   └── graph_api_client.py     # Microsoft Graph API client
│   └── document/
│       ├── generator.py            # python-docx renderer
│       └── jbs_template.docx       # Corporate Word template
├── dashboard/
│   ├── app.py                      # H2O Wave admin dashboard
│   └── cards/
│       ├── interview_monitor.py
│       ├── document_library.py
│       └── sync_manager.py
├── config/
│   ├── settings.py                 # Pydantic settings model
│   └── prompts/
│       ├── system_base.txt
│       ├── phase1.txt
│       ├── phase2.txt
│       ├── phase3.txt
│       ├── phase4.txt
│       └── phase5.txt
├── templates/
│   └── jbs_corporate_template.docx
├── deploy/
│   ├── helm/
│   │   ├── Chart.yaml
│   │   └── values.yaml
│   └── k8s/
│       ├── webhook-deployment.yaml
│       ├── orchestrator-deployment.yaml
│       └── document-generator-deployment.yaml
├── Dockerfile.webhook
├── Dockerfile.orchestrator
├── Dockerfile.document
├── Dockerfile.dashboard
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

```python
import os, time, httpx
from .state_manager import StateManager
from .phase_controller import PhaseController
from .prompt_builder import PromptBuilder
from ..rag.h2ogpte_client import H2OGPTeClient
from ..integrations.mozart_client import MozartClient
from ..document.generator import DocumentGenerator

state_mgr = StateManager()
h2ogpte   = H2OGPTeClient()
mozart    = MozartClient()
doc_gen   = DocumentGenerator()

# In-process OAuth2 token cache for the Bot Framework API
_bot_token_cache: dict = {"token": None, "expires_at": 0}

async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    phase_ctrl = PhaseController(session)
    phase_ctrl.ingest_user_input(msg["text"])

    builder = PromptBuilder(session, phase_ctrl.current_phase)
    response_text, conv_id = await h2ogpte.chat(
        collection_id=session.get("collection_id"),
        conversation_id=session.get("h2ogpte_conv_id"),
        message=msg["text"],
        system_prompt=builder.system_prompt
    )
    session["h2ogpte_conv_id"] = conv_id

    if phase_ctrl.current_phase == 4:
        mozart_site_id = phase_ctrl.extract_mozart_site_id(msg["text"])
        if mozart_site_id:
            session["collected_fields"]["mozart_site_id"] = mozart_site_id
            session["mozart_references"] = await mozart.get_references(mozart_site_id)

    if phase_ctrl.current_phase == 5 and phase_ctrl.is_approved(msg["text"]):
        doc_url = await doc_gen.generate(phase_ctrl.build_jbs_json(session))
        response_text = (
            "Your JBS document has been generated and is ready for download.\n\n"
            f"Download link (valid 15 minutes):\n{doc_url}"
        )
        session["status"] = "complete"

    phase_ctrl.advance_if_complete()
    state_mgr.save(user_id, session)
    await _send_reply(msg, response_text)

async def _send_reply(msg: dict, text: str):
    if msg["channel"] == "teams":
        await _send_teams(
            service_url=msg["service_url"],
            conversation_id=msg["conversation_id"],
            reply_to_id=msg["reply_to"],
            text=text,
        )

async def _send_teams(service_url: str, conversation_id: str, reply_to_id: str, text: str):
    """
    Reply via the Bot Framework REST API.
    URL: {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}
    """
    token = await _get_bot_token()
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{reply_to_id}"
    async with httpx.AsyncClient() as client:
        await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"type": "message", "text": text},
        )

async def _get_bot_token() -> str:
    """Client credentials OAuth2 flow — Bot Framework scope. Token cached with TTL."""
    now = time.time()
    if _bot_token_cache["token"] and now < _bot_token_cache["expires_at"]:
        return _bot_token_cache["token"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     os.environ["TEAMS_APP_ID"],
                "client_secret": os.environ["TEAMS_APP_PASSWORD"],
                "scope":         "https://api.botframework.com/.default",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    _bot_token_cache["token"]      = data["access_token"]
    _bot_token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return _bot_token_cache["token"]
```

---

## 4. State Manager

**File:** `src/agent/state_manager.py`

```python
import json, os, sqlite3
from datetime import datetime, timedelta

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", 24))

class StateManager:
    """
    Three backends selectable via STATE_BACKEND env var:
      memory         — single replica only (dev/test)
      sqlite         — restart-safe, default for HAIC single-replica
      external_redis — multi-replica (use a managed Redis service, not a Helm pod)
    """
    def __init__(self):
        backend = os.environ.get("STATE_BACKEND", "memory")
        if backend == "sqlite":
            self._backend = SQLiteStateBackend()
        elif backend == "external_redis":
            self._backend = ExternalRedisStateBackend()
        else:
            self._backend = MemoryStateBackend()

    def load(self, user_id: str) -> dict:
        return self._backend.load(user_id)

    def save(self, user_id: str, session: dict):
        self._backend.save(user_id, session)
```

---

## 5. Phase Controller

**File:** `src/agent/phase_controller.py`

```python
PHASE_REQUIRED_FIELDS = {
    1: ["customer_name", "site_name", "site_category", "job_purpose"],
    2: ["duties"],
    3: ["hazards", "ppe_requirements", "escalation_procedure"],
    4: ["mozart_site_id"],
    5: []  # Review only
}

SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "collection_corporate",
    "Aviation":    "collection_aviation",
    "Industrial":  "collection_industrial",
    "Maritime":    "collection_maritime",
    "Retail":      "collection_retail",
}

class PhaseController:
    def __init__(self, session: dict):
        self.session      = session
        self.current_phase = session.get("phase", 1)
        self.fields        = session.setdefault("collected_fields", {})

    def ingest_user_input(self, text: str):
        if self.current_phase == 1:
            for cat in SITE_CATEGORY_COLLECTION_MAP:
                if cat.lower() in text.lower():
                    self.fields["site_category"] = cat
                    self.session["collection_id"] = SITE_CATEGORY_COLLECTION_MAP[cat]

    def advance_if_complete(self):
        required = PHASE_REQUIRED_FIELDS.get(self.current_phase, [])
        if all(f in self.fields for f in required):
            self.current_phase += 1
            self.session["phase"] = self.current_phase

    def is_approved(self, user_text: str) -> bool:
        approval_keywords = ["approved", "confirm", "yes", "proceed", "looks good"]
        if self.current_phase == 5:
            return any(k in user_text.lower() for k in approval_keywords)
        return False

    def build_jbs_json(self, session: dict) -> dict:
        from datetime import datetime
        f = session.get("collected_fields", {})
        return {
            "jbs_version": "1.0",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "metadata": {
                "customer_name":  f.get("customer_name", ""),
                "site_name":      f.get("site_name", ""),
                "site_category":  f.get("site_category", ""),
                "job_purpose":    f.get("job_purpose", ""),
                "created_by":     session.get("user_id", ""),
                "authorized_by":  f.get("authorized_by", "")
            },
            "duties":            f.get("duties", []),
            "safety_compliance": f.get("safety_compliance", {}),
            "mozart_references": session.get("mozart_references", {})
        }
```

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

## 8. Mozart Integration Client

**File:** `src/integrations/mozart_client.py`

```python
import os, httpx

class MozartClient:
    def __init__(self):
        self.base_url = os.environ["MOZART_API_BASE_URL"]
        self.api_key  = os.environ["MOZART_API_KEY"]

    async def get_references(self, site_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/sites/{site_id}/documents",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15
            )
            r.raise_for_status()
            data = r.json()
        return {
            "site_id": site_id,
            "reference_documents": [
                {"doc_id": d["id"], "doc_title": d["title"],
                 "doc_type": d.get("type", "SOP"), "mozart_url": d.get("url", "")}
                for d in data.get("documents", [])
            ]
        }
```

---

## 9. Document Generator

**File:** `src/document/generator.py`

```python
import os, json, uuid, boto3
from docx import Document
from docx.shared import Pt, RGBColor

class DocumentGenerator:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

    async def generate(self, jbs_json: dict) -> str:
        doc = Document(os.path.join(os.path.dirname(__file__), "jbs_template.docx"))
        self._populate_document(doc, jbs_json)
        filename  = f"JBS_{jbs_json['metadata']['site_name']}_{uuid.uuid4().hex[:8]}.docx"
        local_path = f"/tmp/{filename}"
        doc.save(local_path)
        s3_key = f"{os.environ.get('S3_PREFIX', 'jbs-documents/')}{filename}"
        self.s3.upload_file(local_path, os.environ["S3_BUCKET"], s3_key)
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["S3_BUCKET"], "Key": s3_key},
            ExpiresIn=int(os.environ.get("DOC_URL_EXPIRY_SECONDS", 900))
        )
```

---

## 10. H2O Wave Admin Dashboard

**File:** `dashboard/app.py`

```python
from h2o_wave import main, app, Q, ui
import httpx, os

ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]

@app("/jbs-dashboard")
async def serve(q: Q):
    if not q.client.initialized:
        await _setup_page(q)
        q.client.initialized = True
    if q.args.refresh_interviews:
        await _load_interviews(q)
    if q.args.trigger_sync:
        await _trigger_sharepoint_sync(q)
    await q.page.save()
```

---

## 11. Prompt Templates

**File:** `config/prompts/system_base.txt`
```
You are the Security Operations Discovery Agent for Certis Security.

CRITICAL RULES:
1. ONLY use information from the retrieved document context. Do not invent tasks, procedures, or compliance requirements.
2. Ask no more than TWO questions per response.
3. If user input is ambiguous, ask a clarifying question before proceeding.
4. You are operating via a Microsoft Teams messaging interface — keep responses concise and structured.
5. Always acknowledge the user's previous answer briefly before asking the next question.
6. Never skip a phase. Follow the interview flow in strict sequence.
```

**File:** `config/prompts/phase1.txt`
```
You are currently in Phase 1: Context & Initiation.

Collect the following fields:
- Customer Name
- Site Name
- Site Category (one of: Corporate, Aviation, Industrial, Maritime, Retail)
- Job Purpose

Start by greeting the user and asking for the Customer Name and Site Name together (maximum 2 questions per message).

When Site Category is provided, confirm it and let the user know you are retrieving relevant SOPs for that category.
```

**File:** `config/prompts/phase2.txt`
```
You are currently in Phase 2: Duty Discovery & Task Sequencing.

Based on the retrieved SOPs for {{site_category}} sites, suggest standard duties and tasks.
For each confirmed task, collect:
- Sequence number
- Trigger (what initiates this task)
- Frequency (how often)
- Responsible Role (which security role performs this)
- Expected Outcome

Present suggested tasks from the knowledge base and ask the user to confirm, edit, or add to them.
```

**File:** `config/prompts/phase3.txt`
```
You are currently in Phase 3: Safety & Compliance.

Collect:
- Site Hazards (list all known hazards)
- PPE Requirements
- Required Skills and Qualifications
- Accreditations required
- Minimum Training requirements
- Incident Escalation procedure
- Reporting Requirements
- Communication Channels

Cross-reference with the retrieved SOPs to ensure no mandatory safety field is missed.
If a required field cannot be determined from user input and is not in the knowledge base, explicitly ask for it.
```

**File:** `config/prompts/phase4.txt`
```
You are currently in Phase 4: Mozart Integration.

Ask the user if there are reference documents (Emergency Plans, SOPs, Policies) stored in Mozart that should be linked to this JBS.

If yes, ask for:
- The Mozart Site ID
- Any specific Document IDs to link

Explain that these will be retrieved automatically and embedded as references in the final document.
```

**File:** `config/prompts/phase5.txt`
```
You are currently in Phase 5: Review & Authorization.

Present a clear, structured summary of ALL collected information covering:
1. Site details (customer, site, category, purpose)
2. All duties and tasks (with sequence, trigger, frequency, role, outcome)
3. Safety & compliance requirements
4. Mozart references (if any)

Ask the user to review and either:
- Type APPROVE or CONFIRM to authorize document generation
- Specify any corrections needed

Do not generate the document until explicit approval is received.
When approved, respond with the approval confirmation. The system will then generate the Word document automatically.
```

---

## 12. Environment Variables Reference

| Variable | Service | Description |
|---|---|---|
| `H2OGPTE_ADDRESS` | Orchestrator | h2oGPTe server URL |
| `H2OGPTE_API_KEY` | Orchestrator | h2oGPTe API key (from H2O Secret Manager) |
| `STATE_BACKEND` | Orchestrator | `memory` \| `sqlite` \| `external_redis` |
| `SQLITE_PATH` | Orchestrator | SQLite file path (when `STATE_BACKEND=sqlite`) |
| `SESSION_TTL_HOURS` | Orchestrator | Session time-to-live in hours (default: 24) |
| `TEAMS_APP_ID` | Webhook + Orchestrator | Azure Bot App Registration client ID |
| `TEAMS_APP_PASSWORD` | Orchestrator | Azure Bot App Registration client secret |
| `MOZART_API_BASE_URL` | Orchestrator | Mozart REST API base URL |
| `MOZART_API_KEY` | Orchestrator | Mozart API authentication key |
| `AZURE_TENANT_ID` | Sync pipeline | Azure AD tenant ID for SharePoint auth |
| `AZURE_CLIENT_ID` | Sync pipeline | Azure AD app client ID |
| `AZURE_CLIENT_SECRET` | Sync pipeline | Azure AD app client secret |
| `SP_SITE_URL` | Sync pipeline | SharePoint site URL |
| `SP_LIBRARY_CORPORATE` | Sync pipeline | SharePoint library ID for Corporate SOPs |
| `SP_LIBRARY_AVIATION` | Sync pipeline | SharePoint library ID for Aviation SOPs |
| `SP_LIBRARY_INDUSTRIAL` | Sync pipeline | SharePoint library ID for Industrial SOPs |
| `SP_LIBRARY_MARITIME` | Sync pipeline | SharePoint library ID for Maritime SOPs |
| `SP_LIBRARY_RETAIL` | Sync pipeline | SharePoint library ID for Retail SOPs |
| `S3_BUCKET` | Document Gen | S3 bucket name for generated documents |
| `S3_ENDPOINT_URL` | Document Gen | S3-compatible endpoint (HAIC object store) |
| `AWS_ACCESS_KEY_ID` | Document Gen | S3 access key |
| `AWS_SECRET_ACCESS_KEY` | Document Gen | S3 secret key |
| `ORCHESTRATOR_URL` | Webhook, Dashboard | Internal URL of orchestrator service |
| `TENANT_ID` | Orchestrator | Certis tenant identifier |
