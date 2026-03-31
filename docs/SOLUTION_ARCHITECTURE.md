# Solution Architecture — JBS Automation Platform

## 1. Architecture Overview

The platform is a multi-layer, cloud-native solution. The three middleware services (webhook, orchestrator, document generator) are deployed on **Azure Container Apps (ACA)**. **H2O Enterprise h2oGPTe** remains on HAIC as the core AI and RAG engine. External integrations cover messaging (Microsoft Teams via Azure Bot Service), document sources (SharePoint), and document generation (python-docx).

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL CHANNELS                                    │
│                                                                             │
│              [Microsoft Teams — Azure Bot Service]                          │
│                              │                                              │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │  HTTPS POST (Bot Framework Activity)
                               │  JWT Bearer token (RS256, validated on receipt)
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AZURE CONTAINER APPS ENVIRONMENT                         │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                 LAYER 1: INGESTION & WEBHOOK SERVICE                 │   │
│  │              (FastAPI — Azure Container Apps, external ingress)      │   │
│  │                                                                      │   │
│  │   • Receives Bot Framework Activity events from Microsoft Teams      │   │
│  │   • Validates RS256 JWT Bearer token (Azure Bot Service JWKS)        │   │
│  │   • Normalises message payload to internal schema                    │   │
│  │   • Routes to Conversation Orchestrator                              │   │
│  └─────────────────────────┬────────────────────────────────────────────┘   │
│                            │                                                │
│                            ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │              LAYER 2: CONVERSATION ORCHESTRATOR                      │   │
│  │           (Python Service — Azure Container Apps, internal)          │   │
│  │                                                                      │   │
│  │   • Maintains per-user conversation state in SQLite                  │   │
│  │   • Enforces 4-phase interview flow (Phase 1 → 4)                    │   │
│  │   • Builds prompt context window for each turn                       │   │
│  │   • Enforces anti-hallucination rules (RAG-only responses)           │   │
│  │   • Dispatches to h2oGPTe API                                        │   │
│  │   • Sends replies via Bot Framework REST API (OAuth2 token cached)   │   │
│  │                                                                      │   │
│  │   State Store: SQLite (default) / external managed Redis             │   │
│  └──────┬───────────────────┬──────────────────────────────────────────┘   │
│                            │                                                │
│                            ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │               LAYER 3: RAG & AI ENGINE                               │   │
│  │         H2O Enterprise h2oGPTe (HAIC Service)                        │   │
│  │                                                                      │   │
│  │  ┌─────────────────┐   ┌───────────────────────┐                     │   │
│  │  │  Collections:   │   │   LLM Inference       │                     │   │
│  │  │  • Corporate    │   │   (llama3/mistral or  │                     │   │
│  │  │  • Aviation     │   │    custom fine-tuned  │                     │   │
│  │  │  • Industrial   │   │    model via LLM      │                     │   │
│  │  │  • Maritime     │   │    Studio)            │                     │   │
│  │  │  • Retail       │   │                       │                     │   │
│  │  └────────┬────────┘   └───────────────────────┘                     │   │
│  │           │  Vector Search (hnswlib)                                 │   │
│  └───────────┼────────────────────────────────────────────────────────┘    │
│              │                                                              │
│              ▼                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │              LAYER 4: KNOWLEDGE BASE PIPELINE                        │   │
│  │                                                                      │   │
│  │   SharePoint Online ──► Microsoft Graph API                          │   │
│  │          │                     │                                     │   │
│  │          ▼                     ▼                                     │   │
│  │   H2O Document AI  ◄─── Raw Documents (PDF/DOCX/XLSX)               │   │
│  │          │           (OCR, entity extraction, chunking)              │   │
│  │          ▼                                                           │   │
│  │   h2oGPTe Collections (per Site Category) — Vector Store             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │              LAYER 5: DOCUMENT GENERATION SERVICE                    │   │
│  │              (Python — deployed as Azure Container Apps)             │   │
│  │                                                                      │   │
│  │   Receives: Approved JBS JSON (<JBS_DATA> payload)                   │   │
│  │   Processes: Maps JSON fields to corporate Word template             │   │
│  │   Outputs: Signed .docx URL (stored in Azure Blob Storage)           │   │
│  │   Notifies: Sends download link back to user via Teams Bot API       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │              LAYER 6: ADMIN DASHBOARD                                │   │
│  │              (H2O Wave — deployed as HAIC App)                       │   │
│  │                                                                      │   │
│  │   • View all active JBS interviews and their current phase           │   │
│  │   • Review and download generated documents                         │   │
│  │   • Manage SharePoint collection sync schedules                      │   │
│  │   • Monitor h2oGPTe usage metrics and RAG quality scores            │   │
│  │   • H2O MLOps: conversation quality + drift monitoring              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Inventory

| Component | Technology | Role | Hosting |
|---|---|---|---|
| Webhook Service | FastAPI (Python) | Receive & validate Teams Bot Framework events | **Azure Container Apps** |
| Conversation Orchestrator | Python service | Phase state machine, prompt builder, Teams reply sender | **Azure Container Apps** |
| Session State | SQLite (default) or external managed Redis | Per-user phase state (TTL: 24h) | **In-process** (no Redis pod) |
| Conversation History | H2O Enterprise h2oGPTe (native) | Full turn history via conversation_id | **h2oGPTe — no Redis needed** |
| AI + RAG Engine | H2O Enterprise h2oGPTe | LLM inference + vector search | HAIC Service (pre-provisioned) |
| Knowledge Base | h2oGPTe Collections | Per-category SOPs and historical JBS | h2oGPTe |
| Document Ingestion Pipeline | H2O Document AI + Graph API | Sync SharePoint → Vector Store | **Azure Container Apps Job (scheduled)** |
| Document Generator | python-docx (Python) | Render approved JSON to .docx | **Azure Container Apps** |
| Admin Dashboard | H2O Wave | Monitoring, management UI | **HAIC App Store** (native — not Helm) |
| Model Monitoring | H2O MLOps | Track inference latency, drift | HAIC Service (pre-provisioned) |
| Object Storage | Azure Blob Storage | Store generated .docx files | Azure Storage |

---

## 4. Data Flow — Conversation Turn

```
1. User sends a message in Microsoft Teams
2. Azure Bot Service delivers signed Bot Framework Activity to Webhook Service (HAIC)
3. Webhook Service validates RS256 JWT Bearer token (against Bot Framework JWKS)
4. Webhook Service extracts text + user AAD object ID, normalises to internal schema
5. Conversation Orchestrator loads user session from SQLite
6. Orchestrator determines current Phase (1–4)
7. Orchestrator builds prompt:
     - System prompt (phase-specific instructions + anti-hallucination rules)
     - Retrieved RAG context (from h2oGPTe Collection for this site category)
     - Conversation history (managed natively by h2oGPTe via conversation_id)
     - User's latest message
8. Orchestrator calls h2oGPTe API → LLM generates response
9. Orchestrator updates SQLite session (new phase state, extracted fields)
10. If Phase 4 (user approves): JSON emitted → Document Generator
11. Document Generator renders .docx, stores in Azure Blob Storage
12. Orchestrator obtains OAuth2 token from Azure AD (Bot Framework scope),
    POSTs reply Activity to Teams via Bot Framework REST API
```

---

## 5. Data Flow — Knowledge Base Sync

```
Scheduled trigger (daily or on-demand via Wave dashboard)
        │
        ▼
SharePoint Online (Graph API)
  • List documents in configured site/library
  • Download changed files since last sync
        │
        ▼
H2O Document AI
  • OCR / text extraction
  • Layout intelligence (tables, headers)
  • Entity extraction (task names, frequencies, roles)
        │
        ▼
h2oGPTe Collection (per site category)
  • Chunk and embed documents
  • Update vector store (hnswlib)
  • Tag chunks with metadata: site_category, doc_type, version
```

---

## 6. Security Architecture

| Concern | Control |
|---|---|
| Webhook authenticity | RS256 JWT Bearer token validation (Azure Bot Service JWKS — `login.botframework.com`) |
| API keys | Azure Key Vault — never in plaintext config |
| User identity | Azure AD Object ID (`aadObjectId`) used as stable user identifier |
| Document access | Azure Blob SAS token URLs (15-minute expiry) |
| SharePoint auth | Azure AD App Registration (OAuth 2.0 client credentials) |
| Teams Bot auth | Azure AD client credentials flow (Bot Framework scope) — token cached in-process |
| Network | Webhook: ACA external ingress (HTTPS). Orchestrator and Document Generator: ACA internal ingress only. |
| RBAC | HAIC role-based access for admin dashboard users |

---

## 7. Conversation Phase State Machine

```
┌──────────────────────────────────────────────────────┐
│                                                      │
│  [START] ──► Phase 1: Context & Initiation           │
│                │  Fields: customer_name, site_name,  │
│                │          site_category, job_purpose  │
│                ▼                                     │
│             Phase 2: Duty Discovery                  │
│                │  Fields: duties[], tasks[]{          │
│                │    sequence, trigger, frequency,    │
│                │    role, outcome}                   │
│                ▼                                     │
│             Phase 3: Safety & Compliance             │
│                │  Fields: hazards[], ppe[],          │
│                │          qualifications[],          │
│                │          escalation_procedure,      │
│                │          reporting_requirements,    │
│                │          comms_channels[]           │
│                ▼                                     │
│             Phase 4: Review & Approval               │
│                │  Actions: present_summary →         │
│                │           await_approval →          │
│                │           emit_JBS_DATA_JSON →      │
│                │           generate_docx             │
│                ▼                                     │
│           [COMPLETE]                                 │
└──────────────────────────────────────────────────────┘
```

---

## 8. JBS JSON Schema (Output)

```json
{
  "jbs_version": "1.0",
  "generated_at": "ISO-8601 timestamp",
  "metadata": {
    "customer_name": "string",
    "site_name": "string",
    "site_category": "Corporate | Aviation | Industrial | Maritime | Retail",
    "job_purpose": "string",
    "created_by": "string (AAD object ID)",
    "authorized_by": "string"
  },
  "duties": [
    {
      "duty_name": "string",
      "tasks": [
        {
          "sequence": "integer",
          "task_description": "string",
          "trigger": "string",
          "frequency": "string",
          "responsible_role": "string",
          "expected_outcome": "string"
        }
      ]
    }
  ],
  "safety_compliance": {
    "site_hazards": ["string"],
    "ppe_requirements": ["string"],
    "required_skills": ["string"],
    "qualifications": ["string"],
    "accreditations": ["string"],
    "minimum_training": ["string"],
    "incident_escalation": "string",
    "reporting_requirements": "string",
    "communication_channels": ["string"]
  }
}
```

---

## 9. Non-Functional Requirements

| NFR | Target |
|---|---|
| Response latency (messaging turn) | < 5 seconds p95 |
| Concurrent interviews | ≥ 50 simultaneous users |
| Knowledge base freshness | SharePoint sync within 24 hours |
| Session persistence | 24-hour TTL (extendable) |
| Document generation time | < 30 seconds |
| Availability | 99.95% (Azure Container Apps SLA) |
| Data residency | Configurable per HAIC deployment region |
