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
│   │   ├── whatsapp.py
│   │   ├── telegram.py
│   │   └── schema.py
│   ├── agent/                      # Conversation orchestrator
│   │   ├── orchestrator.py
│   │   ├── phase_controller.py
│   │   ├── prompt_builder.py
│   │   ├── state_manager.py        # Redis session manager
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
│       ├── document-generator-deployment.yaml
│       └── redis-deployment.yaml
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
from fastapi import FastAPI, Request, HTTPException, Header
import hmac, hashlib, os
from .schema import NormalisedMessage
from .whatsapp import parse_whatsapp_event
from .telegram import parse_telegram_event
import httpx

app = FastAPI()
ORCHESTRATOR_URL = os.environ["ORCHESTRATOR_URL"]

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str = Header(None)
):
    body = await request.body()
    _verify_whatsapp_signature(body, x_hub_signature_256)
    payload = await request.json()
    msg = parse_whatsapp_event(payload)
    if msg:
        await _forward_to_orchestrator(msg)
    return {"status": "ok"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    payload = await request.json()
    msg = parse_telegram_event(payload)
    if msg:
        await _forward_to_orchestrator(msg)
    return {"status": "ok"}

def _verify_whatsapp_signature(body: bytes, signature: str):
    secret = os.environ["WHATSAPP_APP_SECRET"]
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise HTTPException(status_code=403, detail="Invalid signature")

async def _forward_to_orchestrator(msg: NormalisedMessage):
    async with httpx.AsyncClient() as client:
        await client.post(f"{ORCHESTRATOR_URL}/process", json=msg.dict())
```

**File:** `src/webhook/schema.py`

```python
from pydantic import BaseModel
from typing import Optional

class NormalisedMessage(BaseModel):
    channel: str          # "whatsapp" | "telegram"
    user_id: str          # Stable user identifier
    user_name: Optional[str]
    text: str
    timestamp: str        # ISO-8601
    reply_to: str         # channel-specific reply address (phone/chat_id)
```

---

## 3. Conversation Orchestrator

**File:** `src/agent/orchestrator.py`

```python
from .state_manager import StateManager
from .phase_controller import PhaseController
from .prompt_builder import PromptBuilder
from ..rag.h2ogpte_client import H2OGPTeClient
from ..integrations.mozart_client import MozartClient
from ..document.generator import DocumentGenerator
import httpx, os

state_mgr = StateManager()
h2ogpte = H2OGPTeClient()
mozart = MozartClient()
doc_gen = DocumentGenerator()

async def process_message(msg: dict):
    user_id = msg["user_id"]
    session = state_mgr.load(user_id)

    # Determine current phase and update based on user input
    phase_ctrl = PhaseController(session)
    phase_ctrl.ingest_user_input(msg["text"])

    # Build prompt with RAG context
    builder = PromptBuilder(session, phase_ctrl.current_phase)
    prompt = await builder.build(msg["text"])

    # Call h2oGPTe
    response_text = await h2ogpte.chat(
        collection_id=session.get("collection_id"),
        conversation_id=session.get("h2ogpte_conv_id"),
        message=prompt,
        system_prompt=builder.system_prompt
    )

    # Phase 4: Mozart enrichment
    if phase_ctrl.current_phase == 4 and session.get("mozart_site_id"):
        mozart_refs = await mozart.get_references(session["mozart_site_id"])
        session["mozart_references"] = mozart_refs

    # Phase 5: Document generation
    if phase_ctrl.is_approved(response_text):
        jbs_json = phase_ctrl.build_jbs_json(session)
        doc_url = await doc_gen.generate(jbs_json)
        response_text += f"\n\nYour JBS document is ready: {doc_url}"
        session["status"] = "complete"

    # Persist updated session
    state_mgr.save(user_id, session)

    # Send reply
    await _send_reply(msg, response_text)

async def _send_reply(msg: dict, text: str):
    channel = msg["channel"]
    if channel == "whatsapp":
        await _send_whatsapp(msg["reply_to"], text)
    elif channel == "telegram":
        await _send_telegram(msg["reply_to"], text)
```

---

## 4. State Manager (Redis)

**File:** `src/agent/state_manager.py`

```python
import redis, json, os
from datetime import timedelta

SESSION_TTL = timedelta(hours=24)

class StateManager:
    def __init__(self):
        self.r = redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD"),
            ssl=os.environ.get("REDIS_SSL", "true").lower() == "true",
            decode_responses=True
        )

    def _key(self, user_id: str) -> str:
        tenant = os.environ.get("TENANT_ID", "certis")
        return f"{tenant}:session:{user_id}"

    def load(self, user_id: str) -> dict:
        raw = self.r.get(self._key(user_id))
        if raw:
            return json.loads(raw)
        return {
            "phase": 1,
            "collected_fields": {},
            "h2ogpte_conv_id": None,
            "collection_id": None,
            "history": [],
            "status": "active"
        }

    def save(self, user_id: str, session: dict):
        self.r.setex(
            self._key(user_id),
            SESSION_TTL,
            json.dumps(session)
        )

    def delete(self, user_id: str):
        self.r.delete(self._key(user_id))
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
        self.session = session
        self.current_phase = session.get("phase", 1)
        self.fields = session.setdefault("collected_fields", {})

    def ingest_user_input(self, text: str):
        """Extract structured fields from user input via simple NLP rules.
        The LLM response will also be used for richer extraction."""
        # Phase 1: capture site category to select collection
        if self.current_phase == 1:
            for cat in SITE_CATEGORY_COLLECTION_MAP:
                if cat.lower() in text.lower():
                    self.fields["site_category"] = cat
                    self.session["collection_id"] = (
                        SITE_CATEGORY_COLLECTION_MAP[cat]
                    )

    def advance_if_complete(self):
        required = PHASE_REQUIRED_FIELDS.get(self.current_phase, [])
        if all(f in self.fields for f in required):
            self.current_phase += 1
            self.session["phase"] = self.current_phase

    def is_approved(self, llm_response: str) -> bool:
        approval_keywords = ["approved", "confirm", "yes", "proceed", "looks good"]
        if self.current_phase == 5:
            return any(k in llm_response.lower() for k in approval_keywords)
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
            "duties":             f.get("duties", []),
            "safety_compliance":  f.get("safety_compliance", {}),
            "mozart_references":  session.get("mozart_references", {})
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

    async def chat(
        self,
        collection_id: str,
        conversation_id: str | None,
        message: str,
        system_prompt: str
    ) -> str:
        """Send a RAG-grounded chat message to h2oGPTe."""
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
        col = self.client.create_collection(
            name=name,
            description=description
        )
        return col.id

    def ingest_document(self, collection_id: str, file_path: str):
        with open(file_path, "rb") as f:
            upload = self.client.upload(
                file_name=os.path.basename(file_path),
                file=f
            )
        self.client.ingest_uploads(collection_id, [upload.id])
```

---

## 7. SharePoint Sync Pipeline

**File:** `src/rag/sharepoint_sync.py`

```python
import os
from .h2ogpte_client import H2OGPTeClient
from ..integrations.graph_api_client import GraphAPIClient
import tempfile

SITE_CATEGORY_LIBRARY_MAP = {
    "Corporate":   os.environ.get("SP_LIBRARY_CORPORATE"),
    "Aviation":    os.environ.get("SP_LIBRARY_AVIATION"),
    "Industrial":  os.environ.get("SP_LIBRARY_INDUSTRIAL"),
    "Maritime":    os.environ.get("SP_LIBRARY_MARITIME"),
    "Retail":      os.environ.get("SP_LIBRARY_RETAIL"),
}

async def sync_sharepoint_to_h2ogpte():
    graph = GraphAPIClient()
    h2ogpte = H2OGPTeClient()

    for category, library_id in SITE_CATEGORY_LIBRARY_MAP.items():
        if not library_id:
            continue

        collection_id = h2ogpte.get_or_create_collection(
            name=f"collection_{category.lower()}",
            description=f"SOPs and JBS documents for {category} sites"
        )

        # List documents changed in last 24h
        docs = await graph.list_changed_documents(library_id)

        for doc in docs:
            with tempfile.NamedTemporaryFile(
                suffix=doc["name"], delete=False
            ) as tmp:
                content = await graph.download_document(doc["id"])
                tmp.write(content)
                tmp_path = tmp.name

            h2ogpte.ingest_document(collection_id, tmp_path)
            print(f"Synced: {doc['name']} → {category} collection")
```

---

## 8. Mozart Integration Client

**File:** `src/integrations/mozart_client.py`

```python
import os
import httpx

class MozartClient:
    def __init__(self):
        self.base_url = os.environ["MOZART_API_BASE_URL"]
        self.api_key  = os.environ["MOZART_API_KEY"]

    async def get_references(self, site_id: str) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.base_url}/sites/{site_id}/documents",
                headers=headers,
                timeout=15
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
                    "mozart_url": d.get("url", "")
                }
                for d in data.get("documents", [])
            ]
        }
```

---

## 9. Document Generator

**File:** `src/document/generator.py`

```python
import os, json, uuid, boto3
from datetime import datetime
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "jbs_template.docx"
)
S3_BUCKET = os.environ["S3_BUCKET"]
S3_PREFIX = os.environ.get("S3_PREFIX", "jbs-documents/")

class DocumentGenerator:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

    async def generate(self, jbs_json: dict) -> str:
        doc = Document(TEMPLATE_PATH)
        self._populate_document(doc, jbs_json)

        filename = f"JBS_{jbs_json['metadata']['site_name']}_{uuid.uuid4().hex[:8]}.docx"
        local_path = f"/tmp/{filename}"
        doc.save(local_path)

        s3_key = f"{S3_PREFIX}{filename}"
        self.s3.upload_file(local_path, S3_BUCKET, s3_key)

        url = self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=900  # 15 minutes
        )
        return url

    def _populate_document(self, doc: Document, data: dict):
        meta = data["metadata"]
        self._set_bookmark(doc, "CUSTOMER_NAME",  meta["customer_name"])
        self._set_bookmark(doc, "SITE_NAME",      meta["site_name"])
        self._set_bookmark(doc, "SITE_CATEGORY",  meta["site_category"])
        self._set_bookmark(doc, "JOB_PURPOSE",    meta["job_purpose"])
        self._set_bookmark(doc, "GENERATED_AT",   data["generated_at"])

        # Add duties table
        for duty in data.get("duties", []):
            doc.add_heading(duty["duty_name"], level=2)
            table = doc.add_table(rows=1, cols=5)
            table.style = "Table Grid"
            hdr = table.rows[0].cells
            for i, h in enumerate(["#", "Task", "Trigger", "Frequency", "Role"]):
                hdr[i].text = h
            for task in duty.get("tasks", []):
                row = table.add_row().cells
                row[0].text = str(task.get("sequence", ""))
                row[1].text = task.get("task_description", "")
                row[2].text = task.get("trigger", "")
                row[3].text = task.get("frequency", "")
                row[4].text = task.get("responsible_role", "")

        # Safety section
        sc = data.get("safety_compliance", {})
        doc.add_heading("Safety & Compliance", level=1)
        doc.add_paragraph(
            f"Hazards: {', '.join(sc.get('site_hazards', []))}"
        )
        doc.add_paragraph(
            f"PPE: {', '.join(sc.get('ppe_requirements', []))}"
        )

        # Add JBS_DATA JSON as hidden appendix for audit trail
        doc.add_page_break()
        doc.add_heading("Appendix: Machine-Readable Data", level=1)
        p = doc.add_paragraph()
        p.add_run(f"<JBS_DATA>{json.dumps(data)}</JBS_DATA>")
        p.runs[0].font.size = Pt(6)
        p.runs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)  # White (hidden)

    def _set_bookmark(self, doc: Document, bookmark: str, value: str):
        for para in doc.paragraphs:
            if f"{{{bookmark}}}" in para.text:
                para.text = para.text.replace(f"{{{bookmark}}}", value)
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

async def _setup_page(q: Q):
    q.page["meta"] = ui.meta_card(
        box="",
        title="JBS Platform — Admin Dashboard",
        theme="h2o-dark"
    )
    q.page["header"] = ui.header_card(
        box="1 1 12 1",
        title="Certis JBS Automation Platform",
        subtitle="Security Operations Discovery Agent",
        icon="Shield"
    )
    q.page["interviews"] = ui.form_card(
        box="1 2 8 6",
        items=[
            ui.text_xl("Active Interviews"),
            ui.button(name="refresh_interviews", label="Refresh", primary=True),
            ui.table(
                name="interview_table",
                columns=[
                    ui.table_column(name="user", label="User"),
                    ui.table_column(name="site", label="Site"),
                    ui.table_column(name="phase", label="Phase"),
                    ui.table_column(name="status", label="Status"),
                ],
                rows=[]
            )
        ]
    )
    q.page["sync"] = ui.form_card(
        box="9 2 4 4",
        items=[
            ui.text_xl("Knowledge Base Sync"),
            ui.text("Last sync: checking..."),
            ui.button(
                name="trigger_sync",
                label="Sync SharePoint Now",
                primary=False
            ),
        ]
    )
    await _load_interviews(q)

async def _load_interviews(q: Q):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ORCHESTRATOR_URL}/sessions")
        sessions = r.json() if r.status_code == 200 else []

    rows = [
        ui.table_row(
            name=s["user_id"],
            cells=[
                s["user_id"],
                s.get("collected_fields", {}).get("site_name", "—"),
                str(s.get("phase", 1)),
                s.get("status", "active")
            ]
        )
        for s in sessions
    ]
    q.page["interviews"].items[2].table.rows = rows

async def _trigger_sharepoint_sync(q: Q):
    async with httpx.AsyncClient() as client:
        await client.post(f"{ORCHESTRATOR_URL}/admin/sync")
    q.page["sync"].items[1].text.content = "Sync triggered. Running in background."
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
4. You are operating via a mobile messaging interface — keep responses concise and structured.
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
| `H2OGPTE_ADDRESS` | Orchestrator | h2oGPTe server URL (e.g. `https://your-h2ogpte.h2o.ai`) |
| `H2OGPTE_API_KEY` | Orchestrator | h2oGPTe API key (from H2O Secret Manager) |
| `REDIS_HOST` | Orchestrator | Redis hostname |
| `REDIS_PORT` | Orchestrator | Redis port (default 6379) |
| `REDIS_PASSWORD` | Orchestrator | Redis auth password |
| `REDIS_SSL` | Orchestrator | Enable TLS for Redis (`true`/`false`) |
| `WHATSAPP_APP_SECRET` | Webhook | WhatsApp App secret for HMAC validation |
| `WHATSAPP_ACCESS_TOKEN` | Webhook | WhatsApp API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Webhook | WhatsApp Business phone number ID |
| `TELEGRAM_BOT_TOKEN` | Webhook | Telegram Bot API token |
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
| `TENANT_ID` | Orchestrator | Certis tenant identifier for Redis key namespacing |
