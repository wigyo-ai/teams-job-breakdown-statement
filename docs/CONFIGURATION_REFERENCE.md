# Configuration Reference — JBS Automation Platform

## 1. Full Environment Variable Reference

### H2O Platform

| Variable | Required | Default | Description |
|---|---|---|---|
| `H2OGPTE_ADDRESS` | Yes | — | Full URL of your h2oGPTe instance (e.g. `https://gpte.certis.h2o.ai`) |
| `H2OGPTE_API_KEY` | Yes | — | API key for h2oGPTe (store in H2O Secret Manager) |

### Session State (no Redis pod required)

h2oGPTe natively stores full conversation turn history via `conversation_id`. The orchestrator only stores lightweight phase state + collected fields.

| Variable | Required | Default | Description |
|---|---|---|---|
| `STATE_BACKEND` | No | `memory` | `memory` (single replica) \| `sqlite` (restart-safe) \| `external_redis` (multi-replica) |
| `SQLITE_PATH` | No | `/tmp/jbs_sessions.db` | SQLite file path (only used when `STATE_BACKEND=sqlite`) |
| `SESSION_TTL_HOURS` | No | `24` | Session time-to-live in hours |
| `REDIS_URL` | Conditional | — | Full Redis URL (only when `STATE_BACKEND=external_redis`). Use a **managed** Redis service — do NOT deploy a Redis pod via Helm. Format: `rediss://:password@host:6380/0` |

### Messaging Channels

| Variable | Required | Default | Description |
|---|---|---|---|
| `WHATSAPP_APP_SECRET` | Yes | — | Meta app secret for HMAC-SHA256 signature validation |
| `WHATSAPP_ACCESS_TOKEN` | Yes | — | WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | — | WhatsApp Business phone number ID |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram Bot API token from @BotFather |

### Mozart Integration

| Variable | Required | Default | Description |
|---|---|---|---|
| `MOZART_API_BASE_URL` | Yes | — | Base URL of Mozart REST API |
| `MOZART_API_KEY` | Yes | — | Mozart API authentication key |
| `MOZART_TIMEOUT_SECONDS` | No | `15` | Request timeout for Mozart API calls |

### SharePoint / Microsoft Graph API

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | Yes | — | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Yes | — | Azure app registration client ID |
| `AZURE_CLIENT_SECRET` | Yes | — | Azure app registration client secret |
| `SP_SITE_URL` | Yes | — | SharePoint site URL |
| `SP_LIBRARY_CORPORATE` | Yes | — | SharePoint library ID for Corporate category |
| `SP_LIBRARY_AVIATION` | Yes | — | SharePoint library ID for Aviation category |
| `SP_LIBRARY_INDUSTRIAL` | Yes | — | SharePoint library ID for Industrial category |
| `SP_LIBRARY_MARITIME` | Yes | — | SharePoint library ID for Maritime category |
| `SP_LIBRARY_RETAIL` | Yes | — | SharePoint library ID for Retail category |

### Document Generation & Storage

| Variable | Required | Default | Description |
|---|---|---|---|
| `S3_BUCKET` | Yes | — | S3 bucket name for generated .docx files |
| `S3_ENDPOINT_URL` | Yes | — | S3-compatible endpoint (HAIC object store) |
| `S3_PREFIX` | No | `jbs-documents/` | Key prefix for stored documents |
| `AWS_ACCESS_KEY_ID` | Yes | — | S3 access key ID |
| `AWS_SECRET_ACCESS_KEY` | Yes | — | S3 secret access key |
| `DOC_URL_EXPIRY_SECONDS` | No | `900` | Presigned URL expiry (default 15 minutes) |

### Internal Services

| Variable | Required | Default | Description |
|---|---|---|---|
| `ORCHESTRATOR_URL` | Yes | — | Internal URL of the orchestrator service |
| `DOCUMENT_GENERATOR_URL` | Yes | — | Internal URL of the document generator service |
| `TENANT_ID` | No | `certis` | Tenant namespace prefix for Redis keys |

---

## 2. h2oGPTe Collection ID Mapping

Update `src/agent/phase_controller.py` with collection IDs after creating collections in Step 2:

```python
SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "col_xxxxxxxxxxxxxxxx",
    "Aviation":    "col_yyyyyyyyyyyyyyyy",
    "Industrial":  "col_zzzzzzzzzzzzzzzz",
    "Maritime":    "col_aaaaaaaaaaaaaaaa",
    "Retail":      "col_bbbbbbbbbbbbbbbb",
}
```

---

## 3. Conversation Phase Configuration

**File:** `config/settings.py`

```python
PHASE_REQUIRED_FIELDS = {
    1: ["customer_name", "site_name", "site_category", "job_purpose"],
    2: ["duties"],
    3: ["hazards", "ppe_requirements", "escalation_procedure"],
    4: ["mozart_site_id"],
    5: []
}

MAX_HISTORY_TURNS = 20        # Max conversation turns sent to LLM context
MAX_QUESTIONS_PER_TURN = 2    # Anti-hallucination: max questions per response
SESSION_TTL_HOURS = 24
```

---

## 4. Helm Values Quick Reference

The Helm chart manages **3 services only**: webhook, orchestrator, document generator (+ CronJob).
The dashboard is deployed via HAIC App Store — it is not in this chart.

Key values to change in `deploy/helm/values.yaml` for a new environment:

```yaml
global:
  registry: <your HAIC container registry>
  imageTag: "1.0.0"

webhook:
  ingress:
    host: <public webhook hostname>   # Must be accessible by Meta/Telegram

orchestrator:
  env:
    plain:
      STATE_BACKEND: "sqlite"         # or "external_redis" for multi-replica
      # REDIS_URL only needed for external_redis backend (managed service, not Helm pod)

syncCronJob:
  schedule: "0 2 * * *"   # Change to preferred sync time (UTC)
```

---

## 5. Supported Site Categories

The system is pre-configured for these site categories. To add a new category:
1. Create a new h2oGPTe collection
2. Add the library ID env var (e.g. `SP_LIBRARY_HEALTHCARE`)
3. Add the mapping entry in `phase_controller.py`
4. Create corresponding prompt additions in `config/prompts/phase2.txt`

| Category | Environment Variable | Typical SOPs |
|---|---|---|
| Corporate | `SP_LIBRARY_CORPORATE` | Access control, reception, CCTV monitoring |
| Aviation | `SP_LIBRARY_AVIATION` | Airside security, passenger screening, DfT compliance |
| Industrial | `SP_LIBRARY_INDUSTRIAL` | Perimeter patrol, hazmat protocols, contractor access |
| Maritime | `SP_LIBRARY_MARITIME` | Port facility security, ISPS code compliance |
| Retail | `SP_LIBRARY_RETAIL` | Loss prevention, crowd management, cash escort |

---

## 6. Document Template Bookmark Reference

The Word template (`src/document/jbs_template.docx`) must contain these bookmarks (wrapped in `{}`):

| Bookmark | Field |
|---|---|
| `{CUSTOMER_NAME}` | metadata.customer_name |
| `{SITE_NAME}` | metadata.site_name |
| `{SITE_CATEGORY}` | metadata.site_category |
| `{JOB_PURPOSE}` | metadata.job_purpose |
| `{GENERATED_AT}` | generated_at |
| `{AUTHORIZED_BY}` | metadata.authorized_by |

Duty tables and the safety section are dynamically inserted after the template header section. Contact your document template administrator for the approved corporate template file.

---

## 7. JBS JSON Output Schema (Full)

```json
{
  "jbs_version": "1.0",
  "generated_at": "2026-01-15T09:30:00Z",
  "metadata": {
    "customer_name": "Certis Security",
    "site_name": "Changi Airport Terminal 3",
    "site_category": "Aviation",
    "job_purpose": "Provide airside security screening and access control for Terminal 3 operations.",
    "created_by": "user_telegram_123456",
    "authorized_by": "John Smith"
  },
  "duties": [
    {
      "duty_name": "Airside Access Control",
      "tasks": [
        {
          "sequence": 1,
          "task_description": "Conduct opening checks of all access control points",
          "trigger": "Start of shift at 06:00",
          "frequency": "Daily — start of each shift",
          "responsible_role": "Senior Security Officer",
          "expected_outcome": "All ACPs operational and logged in duty register"
        },
        {
          "sequence": 2,
          "task_description": "Verify all personnel entering airside hold valid airside passes",
          "trigger": "Personnel approach ACP",
          "frequency": "Continuous",
          "responsible_role": "Security Officer",
          "expected_outcome": "Unauthorised access prevented; all entries logged"
        }
      ]
    }
  ],
  "safety_compliance": {
    "site_hazards": [
      "Moving aircraft and ground support equipment",
      "Jet blast zones",
      "High-noise environments"
    ],
    "ppe_requirements": [
      "High-visibility vest",
      "Safety boots",
      "Ear defenders (when within 50m of aircraft)"
    ],
    "required_skills": [
      "Access control systems operation",
      "Conflict management",
      "Emergency evacuation procedures"
    ],
    "qualifications": [
      "Security Industry Authority (SIA) Door Supervisor Licence",
      "Aviation Security Training (AVSEC) Level 2"
    ],
    "accreditations": [
      "DfT Aviation Security Programme accreditation",
      "CAA airside safety card"
    ],
    "minimum_training": [
      "40 hours initial AVSEC training",
      "Annual refresher — 8 hours",
      "Site-specific induction — 4 hours"
    ],
    "incident_escalation": "Immediate radio contact to Control Room (Channel 3). Control Room to notify Terminal Duty Manager within 5 minutes. Police contacted for Category A incidents.",
    "reporting_requirements": "All incidents logged in Mozart within 2 hours. Daily occurrence logs submitted to site manager by 23:59.",
    "communication_channels": [
      "Motorola radio — Channel 3 (Control Room)",
      "Mobile phone — Terminal Duty Manager: +65 6xxx xxxx",
      "PABX — Security Control Room ext. 1234"
    ]
  },
  "mozart_references": {
    "site_id": "SITE-T3-001",
    "reference_documents": [
      {
        "doc_id": "MOZ-SOP-AV-001",
        "doc_title": "Airside Access Control Standard Operating Procedure",
        "doc_type": "SOP",
        "mozart_url": "https://mozart.certis.internal/docs/MOZ-SOP-AV-001"
      },
      {
        "doc_id": "MOZ-EP-T3-002",
        "doc_title": "Terminal 3 Emergency Evacuation Plan",
        "doc_type": "Emergency Plan",
        "mozart_url": "https://mozart.certis.internal/docs/MOZ-EP-T3-002"
      }
    ]
  }
}
```
