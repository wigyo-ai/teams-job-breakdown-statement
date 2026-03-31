# Deployment Guide — Certis JBS Automation Platform

## Overview

The platform consists of three middleware services deployed on **Azure Container Apps (ACA)**, an AI/RAG engine on **H2O Enterprise h2oGPTe (HAIC)**, and a Microsoft Teams bot via **Azure Bot Service**.

| Service | Image | Port | Ingress | Entrypoint |
|---|---|---|---|---|
| Webhook | `jbs-webhook` | 8000 | External (public) | `src.webhook.main:app` |
| Orchestrator | `jbs-orchestrator` | 8001 | Internal only | `src.agent.server:app` |
| Document Generator | `jbs-docgen` | 8002 | Internal only | `src.document.server:app` |
| SharePoint Sync | `jbs-orchestrator` (reused) | — | ACA Scheduled Job | `src.rag.sharepoint_sync` |
| Admin Dashboard | `jbs-dashboard` | 10101 | HAIC App Store | `dashboard/app.py` |

**Message flow:** Teams → Azure Bot Service → Webhook (validates JWT) → Orchestrator (4-phase state machine, calls h2oGPTe) → Document Generator (renders .docx, uploads to Azure Blob Storage) → reply via Bot Framework REST API.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Azure CLI v2.55+ | With `containerapp` extension: `az extension add --name containerapp` |
| Docker v24+ | For building and pushing images |
| Python 3.11+ | For local testing and running sync scripts |
| Azure subscription | Contributor access |
| Microsoft 365 tenant | Teams enabled, admin access to Azure AD |
| H2O Enterprise h2oGPTe | Licensed HAIC instance — pre-provisioned, no changes required |
| H2O Document AI | Licensed HAIC instance — pre-provisioned, no changes required |

---

## Step 1 — Clone Repository and Prepare Environment File

```bash
git clone https://github.com/wigyo-ai/certis-job-breakdown-statement.git
cd certis-job-breakdown-statement

cp config/env.template .env
```

Open `.env` and fill in all values. The full variable reference is below — you will retrieve individual values in the steps that follow:

```bash
# ── H2O Enterprise h2oGPTe ──────────────────────────────────────────────────
H2OGPTE_ADDRESS=https://your-h2ogpte-instance.h2o.ai
H2OGPTE_API_KEY=                          # From HAIC Secret Manager

# ── Session state ────────────────────────────────────────────────────────────
# Options: memory (dev only) | sqlite (default) | external_redis (multi-replica)
STATE_BACKEND=sqlite
SQLITE_PATH=/tmp/jbs_sessions.db
SESSION_TTL_HOURS=24
# REDIS_URL=rediss://:password@your-redis.cache.windows.net:6380/0  # only if external_redis

# ── Microsoft Teams — Azure Bot Service ─────────────────────────────────────
TEAMS_APP_ID=                             # Azure Bot App Registration client ID  (Step 5)
TEAMS_APP_PASSWORD=                       # Azure Bot App Registration client secret (Step 5)

# ── SharePoint / Microsoft Graph API ────────────────────────────────────────
AZURE_TENANT_ID=                          # Azure AD tenant ID
AZURE_CLIENT_ID=                          # SharePoint App Registration client ID  (Step 4)
AZURE_CLIENT_SECRET=                      # SharePoint App Registration client secret (Step 4)
SP_SITE_URL=https://certissecurity.sharepoint.com/sites/operations
SP_LIBRARY_CORPORATE=                     # SharePoint document library IDs (Step 3b)
SP_LIBRARY_AVIATION=
SP_LIBRARY_INDUSTRIAL=
SP_LIBRARY_MARITIME=
SP_LIBRARY_RETAIL=

# ── Azure Blob Storage ───────────────────────────────────────────────────────
AZURE_STORAGE_ACCOUNT=certisjbsstorage    # Set after Step 7
AZURE_STORAGE_CONTAINER=certis-jbs-documents
AZURE_STORAGE_KEY=                        # Set after Step 7
BLOB_PREFIX=jbs-documents/
DOC_URL_EXPIRY_SECONDS=900                # SAS URL validity (seconds) — default 15 min

# ── Internal service URLs ────────────────────────────────────────────────────
ORCHESTRATOR_URL=http://localhost:8001    # Overridden by docker-compose / ACA env
DOCUMENT_GENERATOR_URL=http://localhost:8002
TENANT_ID=certis
```

---

## Step 2 — Set Up h2oGPTe Collections

The orchestrator selects a h2oGPTe collection per site category to ground all LLM responses in relevant SOPs. You must create these collections and record their IDs before deploying.

### 2a. Log in to h2oGPTe

Open your HAIC h2oGPTe instance in a browser and sign in.

### 2b. Create one collection per site category

Navigate to **Collections → New Collection** and create:

| Collection Name | Purpose |
|---|---|
| `collection_corporate` | SOPs and JBS for Corporate sites |
| `collection_aviation` | SOPs and JBS for Aviation sites |
| `collection_industrial` | SOPs and JBS for Industrial sites |
| `collection_maritime` | SOPs and JBS for Maritime sites |
| `collection_retail` | SOPs and JBS for Retail sites |

**Record each Collection ID** — you will paste these into `src/agent/phase_controller.py` in Step 14a.

### 2c. Configure SharePoint as a document source

In h2oGPTe, navigate to **Collections → [Select Collection] → Import → SharePoint Online**. Enter:

- **Tenant ID:** your `AZURE_TENANT_ID`
- **Client ID:** your `AZURE_CLIENT_ID` (from Step 4)
- **Client Secret:** your `AZURE_CLIENT_SECRET` (from Step 4)
- **Site URL:** your SharePoint site URL
- **Document Library:** the matching library for this category

Repeat for all five collections. Enable **Auto-sync → Daily**.

> To retrieve SharePoint library IDs: use Graph API Explorer or run `az rest --method get --url "https://graph.microsoft.com/v1.0/sites/{site-id}/drives"`.

### 2d. (Optional) Run initial sync manually

```bash
# With .env loaded
python -m src.rag.sharepoint_sync
```

---

## Step 3 — Configure Azure AD App Registration for SharePoint

This app registration grants the platform read-only access to SharePoint documents via the Microsoft Graph API. Required by `src/integrations/graph_api_client.py`.

> Skip if an app registration for SharePoint access already exists.

1. Azure Portal → **Azure Active Directory → App registrations → New registration**
2. Name: `CertisJBSSharePoint` · Account types: **Single tenant** → **Register**
3. Copy the **Application (client) ID** → this is `AZURE_CLIENT_ID`
4. Under **API permissions → Add a permission → Microsoft Graph → Application permissions**, add:
   - `Files.Read.All`
   - `Sites.Read.All`
5. Click **Grant admin consent**
6. Under **Certificates & secrets → New client secret** → copy the value → this is `AZURE_CLIENT_SECRET`

---

## Step 4 — Define Shell Variables

Set these once — all subsequent steps reference them:

```bash
export RG=certis-jbs-rg
export LOCATION=australiaeast       # change to your preferred region
export ACR_NAME=certisjbsacr        # globally unique, alphanumeric only
export STORAGE_ACCOUNT=certisjbsstorage  # globally unique, 3–24 lowercase alphanumeric
export KV_NAME=certis-jbs-kv        # globally unique
export ACA_ENV=certis-jbs-env
export IMAGE_TAG=1.0.0
```

> **Note:** These variables are not persisted across terminal sessions. Re-export them at the start of any new session before running subsequent steps.

---

## Step 5 — Register Azure Resource Providers

On a new Azure subscription, resource providers must be registered before use. This is a one-time operation per subscription.

```bash
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

Registration is asynchronous. Wait until all providers show `Registered` before proceeding:

```bash
for ns in Microsoft.ContainerRegistry Microsoft.Storage Microsoft.KeyVault Microsoft.App Microsoft.OperationalInsights; do
  echo -n "$ns: "
  az provider show -n $ns --query registrationState -o tsv
done
```

Re-run the check until all five return `Registered` (typically 1–3 minutes each).

---

## Step 6 — Configure Microsoft Teams Bot (Azure Bot Service)

The Webhook service validates RS256 JWT Bearer tokens issued by Azure Bot Service (key published at `https://login.botframework.com/v1/.well-known/keys`). The Orchestrator sends replies via the Bot Framework REST API using OAuth2 client credentials.

### 6a. Create an Azure Bot resource

1. Azure Portal → search **Azure Bot** → **Create**
2. Bot handle: `certis-jbs-bot` · Pricing tier: **Standard**
3. Microsoft App ID: **Create new Microsoft App ID**
4. **Review + create → Create**
5. Once deployed: navigate to the resource → **Configuration**
6. Copy **Microsoft App ID** → this is `TEAMS_APP_ID`
7. Click **Manage** → **Certificates & secrets → New client secret** → copy the value → this is `TEAMS_APP_PASSWORD`

### 6b. Enable the Teams channel

Azure Bot → **Channels → Microsoft Teams → Apply** → accept Terms of Service.

### 6c. Set the messaging endpoint (after Step 12b)

Return to **Azure Bot → Configuration** and set the **Messaging endpoint** to:

```
https://<webhook-app-fqdn>/webhook/teams
```

You will get the FQDN in Step 12b.

### 6d. Add the bot to your Teams tenant

**For production:** Create a Teams app manifest via [Teams Developer Portal](https://dev.teams.microsoft.com), set the Bot ID to `TEAMS_APP_ID`, upload to your tenant app catalogue.

**For testing:** Teams → **Apps → Manage your apps → Upload a custom app** → upload the `.zip` package.

---

## Step 7 — Build Docker Images

Build all images locally. No Azure credentials required at this step.

```bash
# Webhook service — FastAPI on port 8000
docker build -f Dockerfile.webhook -t jbs-webhook:${IMAGE_TAG} .

# Orchestrator — FastAPI on port 8001 (bundles config/prompts/)
docker build -f Dockerfile.orchestrator -t jbs-orchestrator:${IMAGE_TAG} .

# Document generator — FastAPI on port 8002 (bundles templates/)
docker build -f Dockerfile.document -t jbs-docgen:${IMAGE_TAG} .

# H2O Wave admin dashboard — deployed via HAIC App Store, NOT pushed to ACR
docker build -f Dockerfile.dashboard -t jbs-dashboard:${IMAGE_TAG} .
```

Confirm all images built:

```bash
docker images | grep jbs
```

**Test locally before pushing:**

```bash
docker compose up --build
# webhook: http://localhost:8000/health
# orchestrator: http://localhost:8001/health
# docgen: http://localhost:8002/health
```

> `docker-compose.yml` sets `STATE_BACKEND=memory` and wires `ORCHESTRATOR_URL`/`DOCUMENT_GENERATOR_URL` automatically. Requires a populated `.env`.

---

> **Copy-paste note:** All `az` commands in Steps 8–13 are written as single lines. Do not split them across multiple lines when running in the terminal.

## Step 8 — Create Azure Container Registry and Push Images

```bash
az group create --name $RG --location $LOCATION

az acr create --resource-group $RG --name $ACR_NAME --sku Basic --admin-enabled true

az acr login --name $ACR_NAME
export ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)

# Tag and push the three middleware images
docker tag jbs-webhook:${IMAGE_TAG}      ${ACR_LOGIN_SERVER}/jbs-webhook:${IMAGE_TAG}
docker tag jbs-orchestrator:${IMAGE_TAG} ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}
docker tag jbs-docgen:${IMAGE_TAG}       ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}

docker push ${ACR_LOGIN_SERVER}/jbs-webhook:${IMAGE_TAG}
docker push ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}
docker push ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}
```

> The dashboard image is deployed separately via HAIC App Store (Step 14c) and is not pushed to ACR.

---

## Step 9 — Create Azure Blob Storage

The Document Generator uploads `.docx` files to Blob Storage and returns a 15-minute SAS URL to the user. The blob container name and key are read from environment variables `AZURE_STORAGE_CONTAINER` and `AZURE_STORAGE_KEY`.

```bash
az storage account create --name $STORAGE_ACCOUNT --resource-group $RG --location $LOCATION --sku Standard_LRS --kind StorageV2 --allow-blob-public-access false

az storage container create --name certis-jbs-documents --account-name $STORAGE_ACCOUNT --auth-mode login

# Retrieve the storage key — stored in Key Vault in Step 10
export STORAGE_KEY=$(az storage account keys list --account-name $STORAGE_ACCOUNT --resource-group $RG --query "[0].value" -o tsv)
```

Update your `.env`:

```bash
AZURE_STORAGE_ACCOUNT=certisjbsstorage
AZURE_STORAGE_KEY=<paste STORAGE_KEY value>
```

---

## Step 10 — Create Azure Key Vault and Store Secrets

```bash
az keyvault create --name $KV_NAME --resource-group $RG --location $LOCATION --sku standard --enable-rbac-authorization true

# The four secrets used by Container Apps (run each line separately)
az keyvault secret set --vault-name $KV_NAME --name teams-app-password --value "<TEAMS_APP_PASSWORD>"
az keyvault secret set --vault-name $KV_NAME --name h2ogpte-api-key --value "<H2OGPTE_API_KEY>"
az keyvault secret set --vault-name $KV_NAME --name azure-client-secret --value "<AZURE_CLIENT_SECRET>"
az keyvault secret set --vault-name $KV_NAME --name storage-key --value "$STORAGE_KEY"
```

---

## Step 11 — Create Azure Container Apps Environment

```bash
az containerapp env create --name $ACA_ENV --resource-group $RG --location $LOCATION
```

> **Alternative:** The Bicep template at `deploy/azure/main.bicep` provisions Steps 9–13 in a single command. See `deploy/azure/README.md`.

---

## Step 12 — Deploy Services to Azure Container Apps

### 12a. Retrieve ACR credentials

```bash
export ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
export ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)
```

### 12b. Deploy the Webhook service (external ingress — public)

```bash
az containerapp create \
  --name certisjbs-webhook \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image ${ACR_LOGIN_SERVER}/jbs-webhook:${IMAGE_TAG} \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --ingress external \
  --target-port 8000 \
  --min-replicas 2 \
  --max-replicas 10 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars \
    TEAMS_APP_ID="<TEAMS_APP_ID>" \
    ORCHESTRATOR_URL="http://certisjbs-orchestrator" \
  --secrets \
    teams-app-password="<TEAMS_APP_PASSWORD>"
```

Get the FQDN and set the Teams Bot messaging endpoint:

```bash
export WEBHOOK_FQDN=$(az containerapp show \
  --name certisjbs-webhook \
  --resource-group $RG \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Teams Bot messaging endpoint: https://${WEBHOOK_FQDN}/webhook/teams"
```

**→ Paste this URL into Azure Portal → Azure Bot → Configuration → Messaging endpoint** (Step 6c).

### 12c. Deploy the Orchestrator service (internal ingress only)

```bash
az containerapp create \
  --name certisjbs-orchestrator \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG} \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --ingress internal \
  --target-port 8001 \
  --min-replicas 2 \
  --max-replicas 8 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars \
    H2OGPTE_ADDRESS="https://your-h2ogpte-instance.h2o.ai" \
    TEAMS_APP_ID="<TEAMS_APP_ID>" \
    DOCUMENT_GENERATOR_URL="http://certisjbs-docgen" \
    AZURE_TENANT_ID="<AZURE_TENANT_ID>" \
    AZURE_CLIENT_ID="<AZURE_CLIENT_ID>" \
    SP_SITE_URL="https://certissecurity.sharepoint.com/sites/operations" \
    SP_LIBRARY_CORPORATE="<library-id>" \
    SP_LIBRARY_AVIATION="<library-id>" \
    SP_LIBRARY_INDUSTRIAL="<library-id>" \
    SP_LIBRARY_MARITIME="<library-id>" \
    SP_LIBRARY_RETAIL="<library-id>" \
    STATE_BACKEND="sqlite" \
    SQLITE_PATH="/data/jbs_sessions.db" \
    SESSION_TTL_HOURS="24" \
    TENANT_ID="certis" \
  --secrets \
    h2ogpte-api-key="<H2OGPTE_API_KEY>" \
    teams-app-password="<TEAMS_APP_PASSWORD>" \
    azure-client-secret="<AZURE_CLIENT_SECRET>"
```

> For multi-replica deployments: set `STATE_BACKEND=external_redis` and add `--secrets redis-url="<REDIS_URL>"` pointing to an Azure Cache for Redis managed instance.

### 12d. Deploy the Document Generator service (internal ingress only)

```bash
az containerapp create \
  --name certisjbs-docgen \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG} \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --ingress internal \
  --target-port 8002 \
  --min-replicas 1 \
  --max-replicas 4 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars \
    AZURE_STORAGE_ACCOUNT="$STORAGE_ACCOUNT" \
    AZURE_STORAGE_CONTAINER="certis-jbs-documents" \
    BLOB_PREFIX="jbs-documents/" \
    DOC_URL_EXPIRY_SECONDS="900" \
  --secrets \
    storage-key="$STORAGE_KEY"
```

---

## Step 13 — Deploy SharePoint Sync as an ACA Scheduled Job

The sync job reuses the orchestrator image and runs `src/rag/sharepoint_sync.py` daily at 02:00 UTC, fetching changed documents from SharePoint and ingesting them into h2oGPTe collections.

```bash
az containerapp job create \
  --name certisjbs-sp-sync \
  --resource-group $RG \
  --environment $ACA_ENV \
  --trigger-type Schedule \
  --cron-expression "0 2 * * *" \
  --replica-timeout 1800 \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG} \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --cpu 0.5 --memory 1.0Gi \
  --command "python" "-m" "src.rag.sharepoint_sync" \
  --env-vars \
    H2OGPTE_ADDRESS="https://your-h2ogpte-instance.h2o.ai" \
    AZURE_TENANT_ID="<AZURE_TENANT_ID>" \
    AZURE_CLIENT_ID="<AZURE_CLIENT_ID>" \
    SP_SITE_URL="https://certissecurity.sharepoint.com/sites/operations" \
    SP_LIBRARY_CORPORATE="<library-id>" \
    SP_LIBRARY_AVIATION="<library-id>" \
    SP_LIBRARY_INDUSTRIAL="<library-id>" \
    SP_LIBRARY_MARITIME="<library-id>" \
    SP_LIBRARY_RETAIL="<library-id>" \
  --secrets \
    h2ogpte-api-key="<H2OGPTE_API_KEY>" \
    azure-client-secret="<AZURE_CLIENT_SECRET>"
```

---

## Step 14 — Post-Deployment Configuration

### 14a. Update h2oGPTe Collection IDs in the orchestrator

After creating collections in Step 2b, open `src/agent/phase_controller.py` and replace the placeholder values:

```python
SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "col_REPLACE_CORPORATE",   # ← paste real ID from h2oGPTe
    "Aviation":    "col_REPLACE_AVIATION",
    "Industrial":  "col_REPLACE_INDUSTRIAL",
    "Maritime":    "col_REPLACE_MARITIME",
    "Retail":      "col_REPLACE_RETAIL",
}
```

Rebuild and redeploy the orchestrator (and the sync job, which reuses the same image):

```bash
docker build -f Dockerfile.orchestrator \
  -t ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG} .
docker push ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}

az containerapp update \
  --name certisjbs-orchestrator \
  --resource-group $RG \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}

az containerapp job update \
  --name certisjbs-sp-sync \
  --resource-group $RG \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}
```

### 14b. Add the corporate Word template

The Document Generator loads the template from `templates/jbs_corporate_template.docx` at startup. Place the approved corporate template at that path, then rebuild and redeploy:

```bash
# Copy your approved template into the project
cp /path/to/your/template.docx templates/jbs_corporate_template.docx

docker build -f Dockerfile.document \
  -t ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG} .
docker push ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}

az containerapp update \
  --name certisjbs-docgen \
  --resource-group $RG \
  --image ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}
```

### 14c. Deploy the H2O Wave admin dashboard via HAIC App Store

The dashboard is NOT deployed as an Azure Container App — it runs natively inside HAIC.

```bash
cd dashboard
h2o bundle
# Creates: certis-jbs-dashboard-1.0.0.wave
```

1. Log in to HAIC console → **App Store → Import App**
2. Upload `certis-jbs-dashboard-1.0.0.wave`
3. Set visibility: **Private**
4. Set environment variables: `ORCHESTRATOR_URL=https://<certisjbs-orchestrator-internal-url>`
5. Click **Deploy**

### 14d. Trigger the initial SharePoint knowledge base sync

```bash
az containerapp job start \
  --name certisjbs-sp-sync \
  --resource-group $RG

# Monitor execution
az containerapp job execution list \
  --name certisjbs-sp-sync \
  --resource-group $RG \
  --output table
```

---

## Step 15 — Verify Deployment

### 15a. Check all Container Apps are running

```bash
az containerapp list --resource-group $RG --output table
```

Expected:

```
Name                    ProvisioningState
----------------------  -----------------
certisjbs-webhook       Succeeded
certisjbs-orchestrator  Succeeded
certisjbs-docgen        Succeeded
```

### 15b. Webhook health check

```bash
curl https://${WEBHOOK_FQDN}/health
# → {"status": "ok"}
```

### 15c. Confirm orchestrator is reachable from webhook

```bash
az containerapp exec \
  --name certisjbs-webhook \
  --resource-group $RG \
  --command "curl http://certisjbs-orchestrator/health"
# → {"status": "ok"}
```

### 15d. Verify h2oGPTe collections exist and are populated

```bash
python -c "
from src.rag.h2ogpte_client import H2OGPTeClient
c = H2OGPTeClient()
for col in c.client.list_recent_collections(0, 20):
    print(col.id, col.name)
"
```

### 15e. End-to-end Teams test

1. Open Microsoft Teams → find the JBS bot by name
2. Send: `Hello`
3. Bot should greet and ask for Customer Name and Site Name (Phase 1)
4. Complete all 4 phases (Context → Duties → Safety → Review)
5. Type one of the approval keywords: `approve`, `confirm`, `yes`, `proceed`, `looks good`
6. Confirm the reply contains a download link pointing to `*.blob.core.windows.net`

### 15f. Stream live logs

```bash
# Webhook
az containerapp logs show --name certisjbs-webhook --resource-group $RG --follow

# Orchestrator
az containerapp logs show --name certisjbs-orchestrator --resource-group $RG --follow

# Document generator
az containerapp logs show --name certisjbs-docgen --resource-group $RG --follow
```

---

## Step 16 — Set Up GitHub Actions CI/CD

The workflow at `.github/workflows/deploy.yml` builds and pushes all three images on every push to `main` (for paths `src/**`, `config/prompts/**`, `templates/**`, `Dockerfile.*`, `requirements.txt`) and then runs `az containerapp update` for each service.

Add these secrets to your GitHub repository (**Settings → Secrets and variables → Actions**):

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_CLIENT_SECRET` | Service principal client secret |
| `ACR_LOGIN_SERVER` | e.g. `certisjbsacr.azurecr.io` |
| `AZURE_RESOURCE_GROUP` | e.g. `certis-jbs-rg` |

The workflow uses Container App names `certisjbs-webhook`, `certisjbs-orchestrator`, `certisjbs-docgen`, and `certisjbs-sp-sync` — these must match the names used in Steps 12–13.

---

## Step 17 — Production Hardening Checklist

- [ ] All secrets in Azure Key Vault — no plaintext credentials in env-var definitions or CLI history
- [ ] Webhook has external ingress; orchestrator and docgen have **internal-only** ingress
- [ ] Teams Bot RS256 JWT validation confirmed — send a request without a Bearer token, expect HTTP 401
- [ ] Teams app manifest published to tenant app catalogue (not side-loaded)
- [ ] Azure Blob Storage public access disabled (`--allow-blob-public-access false`)
- [ ] SAS token expiry is 15 minutes (`DOC_URL_EXPIRY_SECONDS=900`)
- [ ] SharePoint App Registration scoped to minimum permissions: `Files.Read.All`, `Sites.Read.All` only
- [ ] Azure Key Vault RBAC: `Key Vault Secrets User` role assigned to ACA managed identities only
- [ ] ACR admin credentials rotated post-deployment; switch to managed identity pull for production
- [ ] Container App min/max replica counts reviewed per service
- [ ] h2oGPTe collection IDs replaced in `phase_controller.py` and orchestrator redeployed
- [ ] Corporate Word template (`templates/jbs_corporate_template.docx`) baked into docgen image
- [ ] SharePoint sync job first manual run completed and verified
- [ ] GitHub Actions CI/CD secrets configured and first automated deployment tested
- [ ] H2O MLOps monitoring configured for h2oGPTe inference latency and error rates

---

## Troubleshooting

### Teams bot not responding to messages

1. Check webhook is running: `curl https://${WEBHOOK_FQDN}/health`
2. Check messaging endpoint in Azure Bot → Configuration shows a green status
3. Verify `TEAMS_APP_ID` matches the Microsoft App ID in the Azure Bot resource
4. Check webhook logs:
   ```bash
   az containerapp logs show --name certisjbs-webhook --resource-group $RG --follow
   ```
5. Confirm orchestrator is reachable:
   ```bash
   az containerapp exec --name certisjbs-webhook --resource-group $RG \
     --command "curl http://certisjbs-orchestrator/health"
   ```

### Webhook returns HTTP 401

1. Confirm `TEAMS_APP_ID` is correct — it is the Azure Bot's **Microsoft App ID**, not the resource name
2. Verify the JWKS endpoint is reachable from the webhook container:
   ```bash
   az containerapp exec --name certisjbs-webhook --resource-group $RG \
     --command "curl https://login.botframework.com/v1/.well-known/keys"
   ```
3. Confirm `PyJWT==2.8.0` and `cryptography==42.0.5` are in `requirements.txt` and installed in the image

### Bot cannot send replies (HTTP 401/403 from Bot Framework)

1. Confirm `teams-app-password` secret value is the **client secret**, not the client ID
2. Check the secret has not expired: Azure Portal → App Registration → **Certificates & secrets**
3. Verify the Bot Framework token endpoint is reachable:
   ```bash
   az containerapp exec --name certisjbs-orchestrator --resource-group $RG \
     --command "curl https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
   ```

### Document generation fails

1. Check docgen logs:
   ```bash
   az containerapp logs show --name certisjbs-docgen --resource-group $RG --follow
   ```
2. Verify the blob container exists:
   ```bash
   az storage container exists --name certis-jbs-documents --account-name $STORAGE_ACCOUNT
   ```
3. Confirm `AZURE_STORAGE_KEY` is correct — not expired or rotated
4. Confirm `templates/jbs_corporate_template.docx` is baked into the image (see Step 13b)

### RAG returns no context or hallucinates

1. Verify collections exist and are populated:
   ```bash
   python -c "from src.rag.h2ogpte_client import H2OGPTeClient; c=H2OGPTeClient(); [print(x.id, x.name) for x in c.client.list_recent_collections(0,20)]"
   ```
2. Confirm collection IDs in `src/agent/phase_controller.py` match the actual IDs in h2oGPTe
3. Check Document AI processing status in h2oGPTe UI
4. Confirm SharePoint app registration admin consent is granted

### SharePoint sync job fails or does not run

```bash
# Check job definition and last execution status
az containerapp job show --name certisjbs-sp-sync --resource-group $RG
az containerapp job execution list --name certisjbs-sp-sync --resource-group $RG --output table

# Trigger a manual run to test
az containerapp job start --name certisjbs-sp-sync --resource-group $RG
```
