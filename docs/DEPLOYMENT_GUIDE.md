# Deployment Guide — JBS Automation Platform on Azure Container Apps

## Prerequisites

| Requirement | Details |
|---|---|
| Azure Subscription | Contributor access to an Azure subscription |
| Azure CLI (`az`) | v2.55+ with the `containerapp` extension (`az extension add --name containerapp`) |
| Docker | v24+ (for building and pushing images) |
| Python | 3.11+ |
| Microsoft 365 Tenant | Microsoft Teams enabled |
| Azure Bot Resource | Azure subscription to create an Azure Bot resource |
| Azure AD App Registration | Separate registration with SharePoint Graph API permissions |
| H2O Enterprise h2oGPTe | Licensed instance on HAIC (pre-provisioned — no changes required) |
| H2O Document AI | Licensed instance on HAIC (pre-provisioned — no changes required) |

---

## Step 1 — Clone and Configure

```bash
git clone https://github.com/certis/jbs-platform.git
cd jbs-platform
```

Copy the environment template:

```bash
cp config/env.template .env
```

Edit `.env` and fill in all values (see Configuration Reference for full details):

```bash
# H2O Platform
H2OGPTE_ADDRESS=https://your-h2ogpte-instance.h2o.ai
H2OGPTE_API_KEY=<from H2O Secret Manager>

# Microsoft Teams (Azure Bot Service)
TEAMS_APP_ID=<Azure Bot App Registration client ID>
TEAMS_APP_PASSWORD=<Azure Bot App Registration client secret>

# SharePoint / Azure AD
AZURE_TENANT_ID=<your Azure tenant ID>
AZURE_CLIENT_ID=<SharePoint app registration client ID>
AZURE_CLIENT_SECRET=<SharePoint app registration client secret>
SP_SITE_URL=https://certissecurity.sharepoint.com/sites/operations

# SharePoint library IDs (get from Graph API explorer)
SP_LIBRARY_CORPORATE=<library ID>
SP_LIBRARY_AVIATION=<library ID>
SP_LIBRARY_INDUSTRIAL=<library ID>
SP_LIBRARY_MARITIME=<library ID>
SP_LIBRARY_RETAIL=<library ID>

# Document Storage (Azure Blob Storage)
AZURE_STORAGE_ACCOUNT=certisjbsstorage
AZURE_STORAGE_CONTAINER=certis-jbs-documents
AZURE_STORAGE_KEY=<from Azure Portal after Step 7>

# Internal
TENANT_ID=certis
ORCHESTRATOR_URL=http://jbs-orchestrator:8001
```

---

## Step 2 — Set Up H2O Enterprise h2oGPTe Collections

This step creates the site-category knowledge base collections that power RAG.

### 2a. Log in to h2oGPTe

Open your h2oGPTe instance URL in a browser and log in with your HAIC credentials.

### 2b. Create Collections via h2oGPTe UI

Navigate to **Collections → New Collection** and create one collection per site category:

| Collection Name | Description |
|---|---|
| `collection_corporate` | SOPs and historical JBS for Corporate sites |
| `collection_aviation` | SOPs and historical JBS for Aviation sites |
| `collection_industrial` | SOPs and historical JBS for Industrial sites |
| `collection_maritime` | SOPs and historical JBS for Maritime sites |
| `collection_retail` | SOPs and historical JBS for Retail sites |

> **Note:** Copy each Collection ID from the UI — you will need these to update the `SITE_CATEGORY_COLLECTION_MAP` in `src/agent/phase_controller.py`.

### 2c. Configure SharePoint Integration in h2oGPTe

In h2oGPTe, navigate to **Collections → [Select Collection] → Import → SharePoint Online**.

Enter:
- **Tenant ID:** Your Azure AD tenant ID
- **Client ID:** SharePoint app registration client ID
- **Client Secret:** SharePoint app registration client secret
- **Site URL:** SharePoint site URL
- **Document Library:** Select the appropriate library per category

Enable **Auto-sync** and set frequency to **Daily**.

### 2d. Alternatively, Run Manual Sync via Script

```bash
# From project root, with .env loaded
python -m src.rag.sharepoint_sync
```

---

## Step 3 — Configure Azure AD App Registration for SharePoint

> Skip if your Azure AD app for SharePoint is already configured.

1. Log in to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory → App registrations → New registration**
3. Name: `CertisJBSSharePoint`
4. Supported account types: `Single tenant`
5. Click **Register**
6. Under **API permissions**, add:
   - `Microsoft Graph → Application permissions → Files.Read.All`
   - `Microsoft Graph → Application permissions → Sites.Read.All`
7. Click **Grant admin consent**
8. Under **Certificates & secrets → New client secret** — copy the secret value into `.env` as `AZURE_CLIENT_SECRET`

---

## Step 4 — Configure Microsoft Teams Bot (Azure Bot Service)

This step creates the Azure Bot resource that connects Microsoft Teams to your webhook service.

### 4a. Create an Azure Bot resource

1. Log in to [Azure Portal](https://portal.azure.com)
2. Search for **Azure Bot** → click **Create**
3. Fill in:
   - **Bot handle:** `certis-jbs-bot`
   - **Subscription / Resource Group:** your target subscription
   - **Pricing tier:** `Standard` (for production)
   - **Microsoft App ID:** Select **Create new Microsoft App ID**
4. Click **Review + create → Create**
5. Once deployed, navigate to the resource → **Configuration**
6. Copy the **Microsoft App ID** → this is your `TEAMS_APP_ID`
7. Click **Manage** (next to Microsoft App ID) → **Certificates & secrets → New client secret**
8. Copy the secret value → this is your `TEAMS_APP_PASSWORD`

### 4b. Set the messaging endpoint

You will set this URL after Step 10b (once the webhook Container App is deployed and its FQDN is known). For now, note the placeholder:

```
Messaging endpoint: https://<webhook-app-fqdn>/webhook/teams
```

### 4c. Enable the Teams channel

1. In the Azure Bot resource, navigate to **Channels**
2. Click **Microsoft Teams** → **Apply**
3. Accept the Terms of Service

### 4d. Add the bot to your Teams tenant

Option A — **Publish to your organisation's app catalogue**:
1. Create a Teams app manifest (use [Teams Developer Portal](https://dev.teams.microsoft.com))
2. Set the Bot ID to your `TEAMS_APP_ID`
3. Upload to your tenant's app catalogue or side-load in Teams

Option B — **Side-load for testing**:
1. In Teams → **Apps → Manage your apps → Upload a custom app**
2. Upload your app package `.zip`

---

## Step 5 — Build Docker Images

Build all four service images locally. No Azure credentials are required for this step.

```bash
IMAGE_TAG=1.0.0

# Webhook service
docker build -f Dockerfile.webhook -t jbs-webhook:${IMAGE_TAG} .

# Conversation orchestrator
docker build -f Dockerfile.orchestrator -t jbs-orchestrator:${IMAGE_TAG} .

# Document generator
docker build -f Dockerfile.document -t jbs-docgen:${IMAGE_TAG} .

# H2O Wave admin dashboard
docker build -f Dockerfile.dashboard -t jbs-dashboard:${IMAGE_TAG} .
```

Verify all images built successfully:

```bash
docker images | grep jbs
```

Expected output:
```
jbs-dashboard       1.0.0   ...
jbs-docgen          1.0.0   ...
jbs-orchestrator    1.0.0   ...
jbs-webhook         1.0.0   ...
```

> **Tip:** You can also run all three middleware services locally for pre-deployment testing using `docker compose up --build` (requires a populated `.env` file).

---

## Step 6 — Create Azure Container Registry and Push Images

Define shared variables used throughout the remaining steps:

```bash
RG=certis-jbs-rg
LOCATION=australiaeast        # change to your preferred Azure region
ACR_NAME=certisjbsacr         # must be globally unique, alphanumeric only
IMAGE_TAG=1.0.0
```

Create the resource group and registry:

```bash
az group create --name $RG --location $LOCATION

az acr create \
  --resource-group $RG \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true

az acr login --name $ACR_NAME
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
```

Tag and push the three middleware images to ACR:

```bash
# Webhook service
docker tag jbs-webhook:${IMAGE_TAG}      ${ACR_LOGIN_SERVER}/jbs-webhook:${IMAGE_TAG}
docker push ${ACR_LOGIN_SERVER}/jbs-webhook:${IMAGE_TAG}

# Conversation orchestrator
docker tag jbs-orchestrator:${IMAGE_TAG} ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}
docker push ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}

# Document generator
docker tag jbs-docgen:${IMAGE_TAG}       ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}
docker push ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}
```

> **Note:** The H2O Wave dashboard image (`jbs-dashboard`) is deployed separately via the HAIC App Store (see Step 12c) and is not pushed to ACR.

---

## Step 7 — Create Azure Blob Storage Container

```bash
STORAGE_ACCOUNT=certisjbsstorage   # must be globally unique, 3–24 lowercase alphanumeric

az storage account create \
  --name $STORAGE_ACCOUNT \
  --resource-group $RG \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false

az storage container create \
  --name certis-jbs-documents \
  --account-name $STORAGE_ACCOUNT \
  --auth-mode login

# Retrieve the storage key — will be stored in Key Vault in Step 8
STORAGE_KEY=$(az storage account keys list \
  --account-name $STORAGE_ACCOUNT \
  --resource-group $RG \
  --query "[0].value" -o tsv)
echo "Storage key retrieved — will be stored in Key Vault."
```

---

## Step 8 — Create Azure Key Vault and Store Secrets

```bash
KV_NAME=certis-jbs-kv   # must be globally unique

az keyvault create \
  --name $KV_NAME \
  --resource-group $RG \
  --location $LOCATION \
  --sku standard

# Store all secrets — replace placeholder values with your actual credentials
az keyvault secret set --vault-name $KV_NAME \
  --name teams-app-password   --value "<your Teams bot client secret>"

az keyvault secret set --vault-name $KV_NAME \
  --name h2ogpte-api-key      --value "<your h2oGPTe API key>"

az keyvault secret set --vault-name $KV_NAME \
  --name azure-client-secret  --value "<your SharePoint app registration secret>"

az keyvault secret set --vault-name $KV_NAME \
  --name storage-key          --value "$STORAGE_KEY"
```

---

## Step 9 — Create Azure Container Apps Environment

```bash
ACA_ENV=certis-jbs-env

# Register required resource providers (only needed once per subscription)
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

# Create the Container Apps Environment
az containerapp env create \
  --name $ACA_ENV \
  --resource-group $RG \
  --location $LOCATION
```

---

## Step 10 — Deploy Services to Azure Container Apps

### 10a. Retrieve ACR credentials

```bash
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)
```

### 10b. Deploy the Webhook service (public external ingress)

```bash
az containerapp create \
  --name jbs-webhook \
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
  --cpu 0.5 \
  --memory 1.0Gi \
  --env-vars \
    TEAMS_APP_ID="<your bot app ID>" \
    ORCHESTRATOR_URL="http://jbs-orchestrator" \
  --secrets \
    teams-app-password="<your Teams bot client secret>"
```

Retrieve the webhook FQDN and set it as the Teams Bot messaging endpoint:

```bash
WEBHOOK_FQDN=$(az containerapp show \
  --name jbs-webhook \
  --resource-group $RG \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Set this URL in Azure Bot → Configuration → Messaging endpoint:"
echo "https://${WEBHOOK_FQDN}/webhook/teams"
```

Return to **Azure Portal → Azure Bot → Configuration** and set the messaging endpoint to the URL printed above.

### 10c. Deploy the Orchestrator service (internal ingress only)

```bash
az containerapp create \
  --name jbs-orchestrator \
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
  --cpu 0.5 \
  --memory 1.0Gi \
  --env-vars \
    H2OGPTE_ADDRESS="https://your-h2ogpte-instance.h2o.ai" \
    AZURE_TENANT_ID="<your Azure tenant ID>" \
    AZURE_CLIENT_ID="<SharePoint app registration client ID>" \
    SP_SITE_URL="https://certissecurity.sharepoint.com/sites/operations" \
    SP_LIBRARY_CORPORATE="<library ID>" \
    SP_LIBRARY_AVIATION="<library ID>" \
    SP_LIBRARY_INDUSTRIAL="<library ID>" \
    SP_LIBRARY_MARITIME="<library ID>" \
    SP_LIBRARY_RETAIL="<library ID>" \
    TENANT_ID="certis" \
    STATE_BACKEND="sqlite" \
    SQLITE_PATH="/data/jbs_sessions.db" \
    SESSION_TTL_HOURS="24" \
    TEAMS_APP_ID="<your bot app ID>" \
    DOCUMENT_GENERATOR_URL="http://jbs-docgen" \
  --secrets \
    h2ogpte-api-key="<your h2oGPTe API key>" \
    teams-app-password="<your Teams bot client secret>" \
    azure-client-secret="<your SharePoint app registration secret>"
```

> For multi-replica session state, set `STATE_BACKEND=external_redis` and add a `REDIS_URL` secret pointing to an Azure Cache for Redis instance.

### 10d. Deploy the Document Generator service (internal ingress only)

```bash
az containerapp create \
  --name jbs-docgen \
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
  --cpu 0.5 \
  --memory 1.0Gi \
  --env-vars \
    AZURE_STORAGE_ACCOUNT="$STORAGE_ACCOUNT" \
    AZURE_STORAGE_CONTAINER="certis-jbs-documents" \
    BLOB_PREFIX="jbs-documents/" \
    DOC_URL_EXPIRY_SECONDS="900" \
  --secrets \
    storage-key="$STORAGE_KEY"
```

---

## Step 11 — Deploy SharePoint Sync as ACA Scheduled Job

```bash
az containerapp job create \
  --name jbs-sharepoint-sync \
  --resource-group $RG \
  --environment $ACA_ENV \
  --trigger-type Schedule \
  --cron-expression "0 2 * * *" \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG} \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --replica-timeout 1800 \
  --cpu 0.5 \
  --memory 1.0Gi \
  --command "python" "-m" "src.rag.sharepoint_sync" \
  --env-vars \
    H2OGPTE_ADDRESS="https://your-h2ogpte-instance.h2o.ai" \
    AZURE_TENANT_ID="<your Azure tenant ID>" \
    AZURE_CLIENT_ID="<SharePoint app registration client ID>" \
    SP_SITE_URL="https://certissecurity.sharepoint.com/sites/operations" \
    SP_LIBRARY_CORPORATE="<library ID>" \
    SP_LIBRARY_AVIATION="<library ID>" \
    SP_LIBRARY_INDUSTRIAL="<library ID>" \
    SP_LIBRARY_MARITIME="<library ID>" \
    SP_LIBRARY_RETAIL="<library ID>" \
  --secrets \
    h2ogpte-api-key="<your h2oGPTe API key>" \
    azure-client-secret="<your SharePoint app registration secret>"
```

---

## Step 12 — Post-Deployment Configuration

### 12a. Update h2oGPTe Collection IDs

After creating collections in Step 2b, update the mapping in `src/agent/phase_controller.py`:

```python
SITE_CATEGORY_COLLECTION_MAP = {
    "Corporate":   "col_xxxxxxxx",  # Replace with real collection IDs
    "Aviation":    "col_yyyyyyyy",
    "Industrial":  "col_zzzzzzzz",
    "Maritime":    "col_aaaaaaaa",
    "Retail":      "col_bbbbbbbb",
}
```

Rebuild and redeploy the orchestrator image after this change:

```bash
docker build -f Dockerfile.orchestrator \
  -t ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG} .
docker push ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}

az containerapp update \
  --name jbs-orchestrator \
  --resource-group $RG \
  --image ${ACR_LOGIN_SERVER}/jbs-orchestrator:${IMAGE_TAG}
```

### 12b. Upload the corporate Word template

Place the approved corporate JBS Word template at:

```
templates/jbs_corporate_template.docx
```

Rebuild and redeploy the document generator image:

```bash
docker build -f Dockerfile.document \
  -t ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG} .
docker push ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}

az containerapp update \
  --name jbs-docgen \
  --resource-group $RG \
  --image ${ACR_LOGIN_SERVER}/jbs-docgen:${IMAGE_TAG}
```

### 12c. Deploy the H2O Wave Admin Dashboard

The Wave dashboard is deployed natively in HAIC via the App Store (not to Azure Container Apps).

```bash
cd dashboard
h2o bundle
# Produces: certis-jbs-dashboard-1.0.0.wave
```

1. Log in to HAIC console
2. Navigate to **App Store → Import App**
3. Upload `certis-jbs-dashboard-1.0.0.wave`
4. Set app visibility to **Private**
5. Configure environment variables
6. Click **Deploy**

### 12d. Initial knowledge base population

Trigger a manual SharePoint sync to pre-populate all collections:

```bash
az containerapp job start \
  --name jbs-sharepoint-sync \
  --resource-group $RG

# Monitor the job run
az containerapp job execution list \
  --name jbs-sharepoint-sync \
  --resource-group $RG \
  --output table
```

---

## Step 13 — Verify Deployment

### 13a. Check Container App statuses

```bash
az containerapp list --resource-group $RG --output table
```

Expected output:
```
Name                  ResourceGroup    Location        ProvisioningState
--------------------  ---------------  --------------  -----------------
jbs-webhook           certis-jbs-rg    australiaeast   Succeeded
jbs-orchestrator      certis-jbs-rg    australiaeast   Succeeded
jbs-docgen            certis-jbs-rg    australiaeast   Succeeded
```

### 13b. Test the webhook health endpoint

```bash
curl https://${WEBHOOK_FQDN}/health
# Expected: {"status": "ok"}
```

### 13c. Test h2oGPTe RAG

```bash
python -c "
from src.rag.h2ogpte_client import H2OGPTeClient
import asyncio

async def test():
    client = H2OGPTeClient()
    collections = client.client.list_recent_collections(0, 10)
    print('Collections:', [c.name for c in collections])

asyncio.run(test())
"
```

### 13d. Verify the Teams messaging endpoint

In the Azure Bot resource → **Configuration**, the messaging endpoint should show a green checkmark once the webhook Container App is running.

You can also use the **Test in Web Chat** feature in the Azure Bot resource to send a test message and confirm the bot responds.

### 13e. Send an end-to-end test message via Teams

1. Open Microsoft Teams and find the JBS bot (search by name or via the app catalogue)
2. Send: `Hello`
3. Expected: Bot greets and asks for Customer Name and Site Name
4. Reply with site details and proceed through the 4 phases
5. Approve the JBS summary and confirm the download link points to `*.blob.core.windows.net`

### 13f. Stream service logs

```bash
# Webhook logs
az containerapp logs show \
  --name jbs-webhook \
  --resource-group $RG \
  --follow

# Orchestrator logs
az containerapp logs show \
  --name jbs-orchestrator \
  --resource-group $RG \
  --follow

# Document generator logs
az containerapp logs show \
  --name jbs-docgen \
  --resource-group $RG \
  --follow
```

---

## Step 14 — Production Hardening Checklist

- [ ] All secrets stored in Azure Key Vault — no plaintext credentials in source control or CLI history
- [ ] Webhook Container App has external ingress with HTTPS enforced
- [ ] Orchestrator and Document Generator Container Apps have **internal-only** ingress
- [ ] Teams Bot RS256 JWT Bearer token validation tested — invalid token returns HTTP 401
- [ ] Teams Bot app manifest published to tenant app catalogue (not just side-loaded)
- [ ] Azure Blob Storage container has public access disabled (`--allow-blob-public-access false`)
- [ ] Blob SAS token URLs use 15-minute expiry (`DOC_URL_EXPIRY_SECONDS=900`)
- [ ] SharePoint App Registration uses minimum required permissions: `Files.Read.All`, `Sites.Read.All`
- [ ] Azure Key Vault access policies restrict secret reading to ACA managed identities only
- [ ] ACR admin credentials rotated after initial deployment (use managed identity for production)
- [ ] Container App scaling rules reviewed: min-replicas set correctly per service
- [ ] SharePoint sync ACA Job schedule confirmed and first manual run succeeded
- [ ] h2oGPTe collection IDs updated in `phase_controller.py` and orchestrator redeployed
- [ ] Corporate Word template baked into document generator image and service redeployed
- [ ] H2O MLOps monitoring alerts configured for h2oGPTe inference latency and error rate
- [ ] Penetration test of public webhook Container App endpoint completed

---

## Troubleshooting

### Bot not responding to messages

1. Stream webhook logs:
   ```bash
   az containerapp logs show --name jbs-webhook --resource-group $RG --follow
   ```
2. Verify the Teams messaging endpoint shows a green status in Azure Portal → Azure Bot → Configuration
3. Confirm `ORCHESTRATOR_URL` is reachable from the webhook Container App:
   ```bash
   az containerapp exec --name jbs-webhook --resource-group $RG \
     --command "curl http://jbs-orchestrator/health"
   ```
4. Check `TEAMS_APP_ID` matches the Microsoft App ID shown in the Azure Bot resource

### Webhook returns HTTP 401

1. Confirm `TEAMS_APP_ID` in the webhook env matches the Azure Bot App Registration client ID
2. Verify the JWKS endpoint is reachable from the webhook Container App:
   ```bash
   az containerapp exec --name jbs-webhook --resource-group $RG \
     --command "curl https://login.botframework.com/v1/.well-known/keys"
   ```
3. Check that `PyJWT` and `cryptography` are installed in the webhook image

### Bot cannot send replies (HTTP 401/403 from Teams)

1. Confirm `teams-app-password` secret value is the client secret (not the client ID)
2. Verify the secret has not expired in Azure Portal → App Registration → Certificates & secrets
3. Check orchestrator logs for token acquisition errors:
   ```bash
   az containerapp logs show --name jbs-orchestrator --resource-group $RG --follow
   ```

### RAG returning no context / hallucinating

1. Verify collections are populated:
   ```bash
   python -c "from src.rag.h2ogpte_client import H2OGPTeClient; c=H2OGPTeClient(); print(c.client.list_recent_collections(0,10))"
   ```
2. Check Document AI processing status in h2oGPTe UI
3. Confirm SharePoint Graph API permissions are granted

### Document generation fails

1. Check document generator logs:
   ```bash
   az containerapp logs show --name jbs-docgen --resource-group $RG --follow
   ```
2. Verify Azure Blob Storage container exists and `storage-key` secret is correct
3. Confirm `jbs_corporate_template.docx` is present in the container image

### SharePoint sync not running

1. Check ACA Job status:
   ```bash
   az containerapp job show --name jbs-sharepoint-sync --resource-group $RG
   az containerapp job execution list --name jbs-sharepoint-sync --resource-group $RG --output table
   ```
2. Trigger a manual run:
   ```bash
   az containerapp job start --name jbs-sharepoint-sync --resource-group $RG
   ```
