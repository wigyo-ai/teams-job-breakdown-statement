# Deployment Guide — JBS Automation Platform on H2O AI Cloud

## Prerequisites

| Requirement | Details |
|---|---|
| H2O AI Cloud (HAIC) | Access to a HAIC environment (managed or hybrid) |
| H2O Enterprise h2oGPTe | Licensed instance on HAIC |
| H2O Document AI | Licensed instance on HAIC |
| Docker | v24+ (for building images) |
| kubectl | v1.27+ (configured for HAIC cluster) |
| Helm | v3.12+ |
| Python | 3.11+ |
| Microsoft Teams | Microsoft 365 tenant with Teams enabled |
| Azure Bot Resource | Azure subscription to create an Azure Bot resource |
| Azure AD App | App registration with SharePoint Graph API permissions |
| Mozart API | API key and base URL from your Mozart administrator |

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

Edit `.env` and fill in all values (see Configuration Reference for full reference):

```bash
# H2O Platform
H2OGPTE_ADDRESS=https://your-h2ogpte-instance.h2o.ai
H2OGPTE_API_KEY=<from H2O Secret Manager>

# Microsoft Teams (Azure Bot Service)
TEAMS_APP_ID=<Azure Bot App Registration client ID>
TEAMS_APP_PASSWORD=<Azure Bot App Registration client secret>

# Mozart
MOZART_API_BASE_URL=https://mozart.certis.internal/api/v1
MOZART_API_KEY=<from Mozart admin>

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

# Document storage (HAIC object store or AWS S3)
S3_BUCKET=certis-jbs-documents
S3_ENDPOINT_URL=https://storage.your-haic-cluster.h2o.ai
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>

# Internal
TENANT_ID=certis
ORCHESTRATOR_URL=http://jbs-orchestrator-service:8001
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

In the Azure Bot resource → **Configuration**, set:

```
Messaging endpoint: https://<your-webhook-host>/webhook/teams
```

> The webhook service must be deployed (Step 8) and publicly accessible over HTTPS before Azure Bot Service will accept the endpoint. You can set the URL now and activate it after deployment.

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

```bash
# Webhook service
docker build -f Dockerfile.webhook -t certis-jbs-webhook:1.0.0 .

# Conversation orchestrator
docker build -f Dockerfile.orchestrator -t certis-jbs-orchestrator:1.0.0 .

# Document generator
docker build -f Dockerfile.document -t certis-jbs-docgen:1.0.0 .

# H2O Wave dashboard
docker build -f Dockerfile.dashboard -t certis-jbs-dashboard:1.0.0 .
```

Push to your HAIC container registry:

```bash
REGISTRY=registry.your-haic-cluster.h2o.ai/certis

docker tag certis-jbs-webhook:1.0.0     ${REGISTRY}/jbs-webhook:1.0.0
docker tag certis-jbs-orchestrator:1.0.0 ${REGISTRY}/jbs-orchestrator:1.0.0
docker tag certis-jbs-docgen:1.0.0      ${REGISTRY}/jbs-docgen:1.0.0
docker tag certis-jbs-dashboard:1.0.0   ${REGISTRY}/jbs-dashboard:1.0.0

docker push ${REGISTRY}/jbs-webhook:1.0.0
docker push ${REGISTRY}/jbs-orchestrator:1.0.0
docker push ${REGISTRY}/jbs-docgen:1.0.0
docker push ${REGISTRY}/jbs-dashboard:1.0.0
```

---

## Step 6 — Store Secrets in H2O Secret Manager

Sensitive credentials must be stored in HAIC Secret Manager, not in plaintext config files.

Log in to the HAIC console and navigate to **Secrets**:

```bash
# Using HAIC CLI (h2octl)
h2octl secret create --name h2ogpte-api-key       --value "<your key>"
h2octl secret create --name teams-app-password    --value "<your Teams bot secret>"
h2octl secret create --name mozart-api-key        --value "<your key>"
h2octl secret create --name azure-client-secret   --value "<your SharePoint secret>"
h2octl secret create --name s3-secret-access-key  --value "<your key>"
```

Update `deploy/helm/values.yaml` to reference secrets by name (see Step 7).

---

## Step 7 — Deploy via Helm

### 7a. Review and edit `deploy/helm/values.yaml`

```yaml
global:
  registry: registry.your-haic-cluster.h2o.ai/certis
  imageTag: "1.0.0"
  tenantId: "certis"
  orchestratorUrl: "http://jbs-orchestrator-service:8001"

webhook:
  replicaCount: 2
  image: jbs-webhook
  port: 8000
  ingress:
    host: jbs-webhook.your-haic-cluster.h2o.ai
    tlsSecret: jbs-webhook-tls
  env:
    plain:
      TEAMS_APP_ID: "<your bot app ID>"
    secret:
      TEAMS_APP_PASSWORD:
        name: certis-jbs-secrets
        key: teams-app-password

orchestrator:
  replicaCount: 2
  image: jbs-orchestrator
  port: 8001
  env:
    plain:
      H2OGPTE_ADDRESS: "https://your-h2ogpte-instance.h2o.ai"
      AZURE_TENANT_ID: "<tenant ID>"
      AZURE_CLIENT_ID: "<SharePoint client ID>"
      SP_SITE_URL: "https://certissecurity.sharepoint.com/sites/operations"
      SP_LIBRARY_CORPORATE: "<library ID>"
      SP_LIBRARY_AVIATION: "<library ID>"
      SP_LIBRARY_INDUSTRIAL: "<library ID>"
      SP_LIBRARY_MARITIME: "<library ID>"
      SP_LIBRARY_RETAIL: "<library ID>"
      MOZART_API_BASE_URL: "https://mozart.certis.internal/api/v1"
      TEAMS_APP_ID: "<your bot app ID>"
    secret:
      H2OGPTE_API_KEY:
        name: certis-jbs-secrets
        key: h2ogpte-api-key
      TEAMS_APP_PASSWORD:
        name: certis-jbs-secrets
        key: teams-app-password
      AZURE_CLIENT_SECRET:
        name: certis-jbs-secrets
        key: azure-client-secret
      MOZART_API_KEY:
        name: certis-jbs-secrets
        key: mozart-api-key

documentGenerator:
  replicaCount: 1
  image: jbs-docgen
  port: 8002
  env:
    plain:
      S3_BUCKET: "certis-jbs-documents"
      S3_ENDPOINT_URL: "https://storage.your-haic-cluster.h2o.ai"
      AWS_ACCESS_KEY_ID: "<key ID>"
    secret:
      AWS_SECRET_ACCESS_KEY:
        name: certis-jbs-secrets
        key: s3-secret-access-key

syncCronJob:
  schedule: "0 2 * * *"   # Daily at 02:00 UTC
  image: jbs-orchestrator
  command: ["python", "-m", "src.rag.sharepoint_sync"]
```

### 7b. Install with Helm

```bash
# Create namespace
kubectl create namespace certis-jbs

# Install
helm install certis-jbs deploy/helm/ \
  --namespace certis-jbs \
  --values deploy/helm/values.yaml \
  --wait
```

---

## Step 8 — Deploy H2O Wave Dashboard as HAIC App

H2O Wave apps are deployed natively in HAIC via the App Store.

### 8a. Package the Wave app

```bash
cd dashboard
h2o bundle
# Produces: certis-jbs-dashboard-1.0.0.wave
```

### 8b. Import to HAIC App Store

1. Log in to HAIC console
2. Navigate to **App Store → Import App**
3. Upload `certis-jbs-dashboard-1.0.0.wave`
4. Set app visibility to **Private** (internal users only)
5. Configure environment variables
6. Click **Deploy**

The dashboard will be accessible at your HAIC App Store URL.

---

## Step 9 — Configure H2O MLOps Monitoring

### 9a. Register the JBS inference endpoint

```bash
from h2o_mlops import Client

mlops = Client(url="https://mlops.your-haic-cluster.h2o.ai", token="<token>")

deployment = mlops.create_deployment(
    name="certis-jbs-llm-inference",
    description="JBS Conversation Agent — h2oGPTe inference monitoring"
)
print(f"Deployment ID: {deployment.id}")
```

### 9b. Enable drift monitoring

In the HAIC MLOps console:
1. Navigate to **Deployments → certis-jbs-llm-inference**
2. Enable **Performance Monitoring**
3. Set alert thresholds:
   - Response latency p95 > 8 seconds → alert
   - Error rate > 2% → alert
4. Set up notification channel (email / Teams webhook)

---

## Step 10 — Verify Deployment

### 10a. Check pod health

```bash
kubectl get pods -n certis-jbs
```

Expected output:
```
NAME                                     READY   STATUS    RESTARTS
certis-jbs-webhook-7d4f9b-xxxx           1/1     Running   0
certis-jbs-webhook-7d4f9b-yyyy           1/1     Running   0
certis-jbs-orchestrator-6c8d7b-xxxx      1/1     Running   0
certis-jbs-orchestrator-6c8d7b-yyyy      1/1     Running   0
certis-jbs-docgen-5b6c8a-xxxx            1/1     Running   0
```

### 10b. Test the webhook health endpoint

```bash
curl https://jbs-webhook.your-haic-cluster.h2o.ai/health
# Expected: {"status": "ok"}
```

### 10c. Test h2oGPTe RAG

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

### 10d. Verify the Teams messaging endpoint

In the Azure Bot resource → **Configuration**, the messaging endpoint should show a green checkmark once the webhook service is running.

You can also use the **Test in Web Chat** feature in the Azure Bot resource to send a test message and confirm the bot responds.

### 10e. Send an end-to-end test message via Teams

1. Open Microsoft Teams and find the JBS bot (search by name or via the app catalogue)
2. Send: `Hello`
3. Expected: Bot greets and asks for Customer Name and Site Name
4. Reply with site details and proceed through phases

### 10f. Check admin dashboard

1. Navigate to: `https://jbs-admin.your-haic-cluster.h2o.ai`
2. Verify the test interview appears in the Active Interviews table
3. Verify SharePoint sync status is shown

---

## Step 11 — Post-Deployment Configuration

### 11a. Update h2oGPTe Collection IDs

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

Re-build and re-deploy the orchestrator image after this change.

### 11b. Upload the corporate Word template

Place the approved corporate JBS Word template at:

```
src/document/jbs_template.docx
```

Rebuild the document generator image.

### 11c. Initial knowledge base population

Run the initial SharePoint sync to pre-populate all collections:

```bash
kubectl create job --from=cronjob/certis-jbs-sharepoint-sync \
  initial-sync -n certis-jbs
kubectl logs -f job/initial-sync -n certis-jbs
```

---

## Step 12 — Production Hardening Checklist

- [ ] All secrets stored in H2O Secret Manager (no plaintext in values.yaml)
- [ ] Ingress TLS certificates issued and valid
- [ ] Teams Bot messaging endpoint verified (green status in Azure Portal)
- [ ] JWT Bearer token validation tested (invalid token returns HTTP 401)
- [ ] HAIC RBAC configured: only authorised roles can access admin dashboard
- [ ] S3 bucket policy restricts access to document generator service account only
- [ ] SharePoint App Registration uses minimum required permissions (`Files.Read.All`, `Sites.Read.All`)
- [ ] Mozart API key rotated from default
- [ ] MLOps monitoring alerts configured and tested
- [ ] Pod resource limits set in Helm values (CPU/memory)
- [ ] Horizontal Pod Autoscaler (HPA) configured for webhook and orchestrator services
- [ ] Document download URLs use short-lived presigned URLs (15 minutes)
- [ ] Penetration test of webhook endpoint completed
- [ ] Teams Bot app manifest published to tenant app catalogue (not just side-loaded)

---

## Troubleshooting

### Bot not responding to messages

1. Check webhook service logs:
   ```bash
   kubectl logs -l app=jbs-webhook -n certis-jbs --tail=50
   ```
2. Verify the Teams messaging endpoint shows a green status in Azure Portal → Azure Bot → Configuration
3. Confirm `ORCHESTRATOR_URL` is reachable from webhook pod:
   ```bash
   kubectl exec -it deploy/certis-jbs-webhook -n certis-jbs -- \
     curl http://jbs-orchestrator-service:8001/health
   ```
4. Check `TEAMS_APP_ID` matches the Microsoft App ID shown in the Azure Bot resource

### Webhook returns HTTP 401

1. Confirm `TEAMS_APP_ID` in the webhook env matches the Azure Bot App Registration
2. Verify the JWKS endpoint is reachable from the webhook pod:
   ```bash
   kubectl exec -it deploy/certis-jbs-webhook -n certis-jbs -- \
     curl https://login.botframework.com/v1/.well-known/keys
   ```
3. Check that `PyJWT` and `cryptography` are installed in the webhook image

### Bot cannot send replies (HTTP 401/403 from Teams)

1. Confirm `TEAMS_APP_PASSWORD` is the client secret (not the client ID)
2. Verify the secret has not expired in Azure Portal → App Registration → Certificates & secrets
3. Check orchestrator logs for token acquisition errors:
   ```bash
   kubectl logs -l app=jbs-orchestrator -n certis-jbs --tail=50
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
   kubectl logs -l app=jbs-docgen -n certis-jbs --tail=50
   ```
2. Verify S3 bucket exists and credentials are correct
3. Confirm `jbs_template.docx` is present in the container image

### SharePoint sync not running

1. Check CronJob status:
   ```bash
   kubectl get cronjobs -n certis-jbs
   kubectl get jobs -n certis-jbs
   ```
2. Run manual sync:
   ```bash
   kubectl create job --from=cronjob/certis-jbs-sharepoint-sync manual-sync-$(date +%s) -n certis-jbs
   ```
