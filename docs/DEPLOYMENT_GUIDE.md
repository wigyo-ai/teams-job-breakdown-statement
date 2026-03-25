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
| WhatsApp Business Account | Meta Developer account with approved WhatsApp Business API |
| Telegram Bot | Bot token from @BotFather |
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

Edit `.env` and fill in all values (see Technical Design §12 for full reference):

```bash
# H2O Platform
H2OGPTE_ADDRESS=https://your-h2ogpte-instance.h2o.ai
H2OGPTE_API_KEY=<from H2O Secret Manager>

# Redis
REDIS_HOST=jbs-redis-service
REDIS_PORT=6379
REDIS_PASSWORD=<generate a strong password>
REDIS_SSL=false   # set true for production

# WhatsApp
WHATSAPP_APP_SECRET=<from Meta Developer Portal>
WHATSAPP_ACCESS_TOKEN=<permanent token from Meta>
WHATSAPP_PHONE_NUMBER_ID=<from Meta Developer Portal>

# Telegram
TELEGRAM_BOT_TOKEN=<from @BotFather>

# Mozart
MOZART_API_BASE_URL=https://mozart.certis.internal/api/v1
MOZART_API_KEY=<from Mozart admin>

# SharePoint / Azure AD
AZURE_TENANT_ID=<your Azure tenant ID>
AZURE_CLIENT_ID=<app registration client ID>
AZURE_CLIENT_SECRET=<app registration client secret>
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
- **Client ID:** Azure app registration client ID
- **Client Secret:** Azure app registration client secret
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

> Skip if your Azure AD app is already configured.

1. Log in to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory → App registrations → New registration**
3. Name: `CertisJBSPlatform`
4. Supported account types: `Single tenant`
5. Click **Register**
6. Under **API permissions**, add:
   - `Microsoft Graph → Application permissions → Files.Read.All`
   - `Microsoft Graph → Application permissions → Sites.Read.All`
7. Click **Grant admin consent**
8. Under **Certificates & secrets → New client secret** — copy the secret value into `.env`

---

## Step 4 — Configure WhatsApp Business API

1. Log in to [Meta Developer Portal](https://developers.facebook.com)
2. Create an App → Business type
3. Add **WhatsApp** product
4. Under **WhatsApp → Getting Started**, note:
   - Phone Number ID → `WHATSAPP_PHONE_NUMBER_ID`
   - Temporary access token (generate a permanent system user token for production) → `WHATSAPP_ACCESS_TOKEN`
5. Under **WhatsApp → Configuration → Webhook**:
   - Callback URL: `https://your-jbs-webhook.h2o.ai/webhook/whatsapp`
   - Verify Token: set a random string — note it for webhook verification
   - Subscribe to: `messages`
6. Copy **App Secret** from **App Settings → Basic** → `WHATSAPP_APP_SECRET`

---

## Step 5 — Configure Telegram Bot

1. Open Telegram and message `@BotFather`
2. Send `/newbot` and follow prompts to name your bot
3. Copy the API token → `TELEGRAM_BOT_TOKEN`
4. After deploying the webhook service (Step 8), register the webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-jbs-webhook.h2o.ai/webhook/telegram"}'
```

---

## Step 6 — Build Docker Images

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

## Step 7 — Store Secrets in H2O Secret Manager

Sensitive credentials must be stored in HAIC Secret Manager, not in plaintext config files.

Log in to the HAIC console and navigate to **Secrets**:

```bash
# Using HAIC CLI (h2octl)
h2octl secret create --name h2ogpte-api-key          --value "<your key>"
h2octl secret create --name whatsapp-app-secret      --value "<your secret>"
h2octl secret create --name whatsapp-access-token    --value "<your token>"
h2octl secret create --name telegram-bot-token       --value "<your token>"
h2octl secret create --name mozart-api-key           --value "<your key>"
h2octl secret create --name azure-client-secret      --value "<your secret>"
h2octl secret create --name redis-password           --value "<your password>"
h2octl secret create --name s3-secret-access-key     --value "<your key>"
```

Update `deploy/helm/values.yaml` to reference secrets by name (see Step 8).

---

## Step 8 — Deploy via Helm

### 8a. Review and edit `deploy/helm/values.yaml`

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
    WHATSAPP_PHONE_NUMBER_ID: "<your phone number ID>"  # non-secret
    WHATSAPP_APP_SECRET:
      secretName: whatsapp-app-secret
      secretKey: value
    WHATSAPP_ACCESS_TOKEN:
      secretName: whatsapp-access-token
      secretKey: value
    TELEGRAM_BOT_TOKEN:
      secretName: telegram-bot-token
      secretKey: value

orchestrator:
  replicaCount: 2
  image: jbs-orchestrator
  port: 8001
  env:
    H2OGPTE_ADDRESS: "https://your-h2ogpte-instance.h2o.ai"
    AZURE_TENANT_ID: "<tenant ID>"
    AZURE_CLIENT_ID: "<client ID>"
    SP_SITE_URL: "https://certissecurity.sharepoint.com/sites/operations"
    SP_LIBRARY_CORPORATE: "<library ID>"
    SP_LIBRARY_AVIATION: "<library ID>"
    SP_LIBRARY_INDUSTRIAL: "<library ID>"
    SP_LIBRARY_MARITIME: "<library ID>"
    SP_LIBRARY_RETAIL: "<library ID>"
    MOZART_API_BASE_URL: "https://mozart.certis.internal/api/v1"
    H2OGPTE_API_KEY:
      secretName: h2ogpte-api-key
      secretKey: value
    AZURE_CLIENT_SECRET:
      secretName: azure-client-secret
      secretKey: value
    MOZART_API_KEY:
      secretName: mozart-api-key
      secretKey: value

redis:
  enabled: true
  image: redis:7-alpine
  port: 6379
  persistence:
    enabled: true
    size: 1Gi
  auth:
    secretName: redis-password
    secretKey: value

documentGenerator:
  replicaCount: 1
  image: jbs-docgen
  port: 8002
  env:
    S3_BUCKET: "certis-jbs-documents"
    S3_ENDPOINT_URL: "https://storage.your-haic-cluster.h2o.ai"
    AWS_ACCESS_KEY_ID: "<key ID>"
    S3_SECRET_ACCESS_KEY:
      secretName: s3-secret-access-key
      secretKey: value

dashboard:
  replicaCount: 1
  image: jbs-dashboard
  port: 10101
  ingress:
    host: jbs-admin.your-haic-cluster.h2o.ai
    tlsSecret: jbs-dashboard-tls

syncCronJob:
  schedule: "0 2 * * *"   # Daily at 02:00 UTC
  image: jbs-orchestrator
  command: ["python", "-m", "src.rag.sharepoint_sync"]
```

### 8b. Install with Helm

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

## Step 9 — Deploy H2O Wave Dashboard as HAIC App

H2O Wave apps are deployed natively in HAIC via the App Store.

### 9a. Package the Wave app

```bash
cd dashboard
h2o bundle
# Produces: certis-jbs-dashboard-1.0.0.wave
```

### 9b. Import to HAIC App Store

1. Log in to HAIC console
2. Navigate to **App Store → Import App**
3. Upload `certis-jbs-dashboard-1.0.0.wave`
4. Set app visibility to **Private** (internal users only)
5. Configure environment variables (same as Helm `dashboard` section above)
6. Click **Deploy**

The dashboard will be accessible at your HAIC App Store URL.

---

## Step 10 — Configure H2O MLOps Monitoring

### 10a. Register the JBS inference endpoint

```bash
# Using h2o MLOps Python SDK
from h2o_mlops import Client

mlops = Client(url="https://mlops.your-haic-cluster.h2o.ai", token="<token>")

# Create deployment record for monitoring
deployment = mlops.create_deployment(
    name="certis-jbs-llm-inference",
    description="JBS Conversation Agent — h2oGPTe inference monitoring"
)

print(f"Deployment ID: {deployment.id}")
```

### 10b. Enable drift monitoring

In the HAIC MLOps console:
1. Navigate to **Deployments → certis-jbs-llm-inference**
2. Enable **Performance Monitoring**
3. Set alert thresholds:
   - Response latency p95 > 8 seconds → alert
   - Error rate > 2% → alert
4. Set up notification channel (email / Slack)

---

## Step 11 — Verify Deployment

### 11a. Check pod health

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
certis-jbs-redis-0                       1/1     Running   0
```

### 11b. Test webhook endpoint

```bash
curl -X POST https://jbs-webhook.your-haic-cluster.h2o.ai/webhook/telegram \
  -H "Content-Type: application/json" \
  -d '{
    "update_id": 1,
    "message": {
      "message_id": 1,
      "from": {"id": 999, "first_name": "Test"},
      "chat": {"id": 999, "type": "private"},
      "text": "Hello"
    }
  }'
```

Expected response: `{"status": "ok"}`

### 11c. Test h2oGPTe RAG

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

### 11d. Send an end-to-end test message via Telegram

1. Open Telegram and find your bot
2. Send: `Hello`
3. Expected: Bot greets and asks for Customer Name and Site Name
4. Reply with site details and proceed through phases

### 11e. Check admin dashboard

1. Navigate to: `https://jbs-admin.your-haic-cluster.h2o.ai`
2. Verify the test interview appears in the Active Interviews table
3. Verify SharePoint sync status is shown

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

Re-build and re-deploy the orchestrator image after this change.

### 12b. Upload the corporate Word template

Place the approved corporate JBS Word template (with `{CUSTOMER_NAME}`, `{SITE_NAME}` etc. bookmarks) at:

```
src/document/jbs_template.docx
```

Rebuild the document generator image.

### 12c. Initial knowledge base population

Run the initial SharePoint sync to pre-populate all collections:

```bash
kubectl create job --from=cronjob/certis-jbs-sharepoint-sync \
  initial-sync -n certis-jbs
kubectl logs -f job/initial-sync -n certis-jbs
```

---

## Step 13 — Production Hardening Checklist

- [ ] All secrets stored in H2O Secret Manager (no plaintext in values.yaml)
- [ ] Redis SSL enabled (`REDIS_SSL=true`)
- [ ] Ingress TLS certificates issued and valid
- [ ] WhatsApp webhook signature validation tested
- [ ] HAIC RBAC configured: only authorised roles can access admin dashboard
- [ ] S3 bucket policy restricts access to document generator service account only
- [ ] SharePoint App Registration uses minimum required permissions (`Files.Read.All`, `Sites.Read.All`)
- [ ] Mozart API key rotated from default
- [ ] MLOps monitoring alerts configured and tested
- [ ] Redis persistence enabled with regular snapshots
- [ ] Pod resource limits set in Helm values (CPU/memory)
- [ ] Horizontal Pod Autoscaler (HPA) configured for webhook and orchestrator services
- [ ] Document download URLs use short-lived presigned URLs (15 minutes)
- [ ] Penetration test of webhook endpoint completed

---

## Troubleshooting

### Bot not responding to messages

1. Check webhook service logs:
   ```bash
   kubectl logs -l app=jbs-webhook -n certis-jbs --tail=50
   ```
2. Verify WhatsApp webhook subscription is active in Meta Developer Portal
3. Confirm `ORCHESTRATOR_URL` is reachable from webhook pod:
   ```bash
   kubectl exec -it deploy/certis-jbs-webhook -n certis-jbs -- \
     curl http://jbs-orchestrator-service:8001/health
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

### Redis session loss

1. Confirm Redis pod is running with persistent volume:
   ```bash
   kubectl get pvc -n certis-jbs
   ```
2. Check Redis password secret is correctly mounted

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
