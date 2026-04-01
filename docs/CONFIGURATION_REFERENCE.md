# Configuration Reference — JBS Automation Platform

## 1. Full Environment Variable Reference

### H2O Platform

| Variable | Required | Default | Description |
|---|---|---|---|
| `H2OGPTE_ADDRESS` | Yes | — | Full URL of your h2oGPTe instance (e.g. `https://gpte.certis.h2o.ai`) |
| `H2OGPTE_API_KEY` | Yes | — | API key for h2oGPTe (store in Azure Key Vault) |

### Session State (no Redis pod required)

h2oGPTe natively stores full conversation turn history via `conversation_id`. The orchestrator only stores lightweight phase state + collected fields.

| Variable | Required | Default | Description |
|---|---|---|---|
| `STATE_BACKEND` | No | `memory` | `memory` (single replica) \| `sqlite` (restart-safe) \| `external_redis` (multi-replica) |
| `SQLITE_PATH` | No | `/tmp/jbs_sessions.db` | SQLite file path (only used when `STATE_BACKEND=sqlite`) |
| `SESSION_TTL_HOURS` | No | `24` | Session time-to-live in hours |
| `REDIS_URL` | Conditional | — | Full Redis URL (only when `STATE_BACKEND=external_redis`). Use a **managed** Redis service — do NOT deploy a Redis pod via Helm. Format: `rediss://:password@host:6380/0` |

### Microsoft Teams — Azure Bot Service

| Variable | Required | Default | Description |
|---|---|---|---|
| `TEAMS_APP_ID` | Yes | — | Application (client) ID of the Azure Bot App Registration. Used for JWT audience validation (webhook) and OAuth2 client credentials (orchestrator). |
| `TEAMS_APP_PASSWORD` | Yes | — | Client secret of the Azure Bot App Registration. Used to obtain Bot Framework OAuth2 access tokens for sending replies. |

> **How to obtain these values:**
> 1. Go to [Azure Portal](https://portal.azure.com) → **Azure Bot** resource (or create one)
> 2. Under **Configuration**, the **Microsoft App ID** is your `TEAMS_APP_ID`
> 3. Click **Manage** → **Certificates & secrets** → create a new client secret → copy it as `TEAMS_APP_PASSWORD`
> 4. Set the **Messaging endpoint** to `https://<your-webhook-host>/webhook/teams`

### SharePoint / Microsoft Graph API

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | Yes | — | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Yes | — | Azure app registration client ID (for SharePoint access) |
| `AZURE_CLIENT_SECRET` | Yes | — | Azure app registration client secret (for SharePoint access) |
| `SP_SITE_URL` | Yes | — | SharePoint site URL |
| `SP_LIBRARY_CORPORATE` | Yes | — | SharePoint library ID for Corporate category |
| `SP_LIBRARY_AVIATION` | Yes | — | SharePoint library ID for Aviation category |
| `SP_LIBRARY_INDUSTRIAL` | Yes | — | SharePoint library ID for Industrial category |
| `SP_LIBRARY_MARITIME` | Yes | — | SharePoint library ID for Maritime category |
| `SP_LIBRARY_RETAIL` | Yes | — | SharePoint library ID for Retail category |

> **Note:** The SharePoint App Registration (`AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`) is separate from the Teams Bot App Registration (`TEAMS_APP_ID` / `TEAMS_APP_PASSWORD`). The SharePoint registration needs `Files.Read.All` and `Sites.Read.All` Graph API permissions. The Teams Bot registration needs no Graph API permissions — it uses the Bot Framework scope only.

### Document Generation & Storage (Azure Blob Storage)

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_STORAGE_ACCOUNT` | Yes | — | Azure Storage account name |
| `AZURE_STORAGE_CONTAINER` | Yes | `certis-jbs-documents` | Blob container name |
| `AZURE_STORAGE_KEY` | Yes | — | Storage account access key (store in Azure Key Vault) |
| `BLOB_PREFIX` | No | `jbs-documents/` | Blob name prefix for stored documents |
| `DOC_URL_EXPIRY_SECONDS` | No | `900` | SAS token URL expiry in seconds (default 15 minutes) |

> **How to obtain:** After completing Step 7 in the Deployment Guide (Create Azure Blob Storage), the account name is the name you chose, the container name is `certis-jbs-documents`, and the key is found under **Storage Account → Access keys** in the Azure Portal.

### Internal Services

| Variable | Required | Default | Description |
|---|---|---|---|
| `ORCHESTRATOR_URL` | Yes | — | Internal URL of the orchestrator service |
| `DOCUMENT_GENERATOR_URL` | Yes | — | Internal URL of the document generator service |
| `TENANT_ID` | No | `certis` | Tenant namespace prefix |

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
    4: [],
}

MAX_HISTORY_TURNS = 20        # Max conversation turns sent to LLM context
MAX_QUESTIONS_PER_TURN = 2    # Anti-hallucination: max questions per response
SESSION_TTL_HOURS = 24
```

---

## 4. Azure Container Apps Deployment Quick Reference

The primary deployment uses the Bicep template (`deploy/azure/main.bicep`) or `az containerapp` CLI commands — see `docs/DEPLOYMENT_GUIDE.md`.

Key parameter values to set for a new environment:

```bash
# Core identifiers
PREFIX=certisjbs
IMAGE_TAG=1.0.0
ACR_LOGIN_SERVER=certisjbsacr.azurecr.io

# Teams Bot (Azure Bot App Registration)
TEAMS_APP_ID=<your-bot-app-id>
TEAMS_APP_PASSWORD=<your-bot-app-secret>

# h2oGPTe
H2OGPTE_ADDRESS=https://your-h2ogpte-instance.h2o.ai
H2OGPTE_API_KEY=<your-h2ogpte-api-key>

# SharePoint (separate App Registration)
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<sharepoint-client-id>
AZURE_CLIENT_SECRET=<sharepoint-client-secret>
SP_SITE_URL=https://wigyoai.sharepoint.com/sites/h2O

# Azure Blob Storage
AZURE_STORAGE_ACCOUNT=certisjbsstorage
AZURE_STORAGE_CONTAINER=certis-jbs-documents
AZURE_STORAGE_KEY=<storage-access-key>

# State backend (sqlite for single-replica, external_redis for multi-replica)
STATE_BACKEND=sqlite
SQLITE_PATH=/data/jbs_sessions.db
```

> **Note:** The `deploy/helm/` directory contains a reference chart mapping these same variables to Helm values format. It is provided for reference only — the primary deployment uses Bicep + Azure CLI, not Helm.

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
    "created_by": "aad-object-id-of-user",
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
    "reporting_requirements": "All incidents logged in the incident management system within 2 hours. Daily occurrence logs submitted to site manager by 23:59.",
    "communication_channels": [
      "Motorola radio — Channel 3 (Control Room)",
      "Mobile phone — Terminal Duty Manager: +65 6xxx xxxx",
      "PABX — Security Control Room ext. 1234"
    ]
  }
}
```
