#!/bin/bash
set -e

RG=certis-jbs-rg

echo "==> Fixing orchestrator env vars..."
az containerapp update \
  --name certisjbs-orchestrator \
  --resource-group $RG \
  --set-env-vars \
    "H2OGPTE_ADDRESS=https://h2ogpte.cloud-dev.h2o.dev" \
    "TEAMS_APP_ID=9e8400c3-7360-4475-8688-0538233df529" \
    "AZURE_TENANT_ID=35013e61-d285-4f21-9b33-4c601cc1d8ce" \
    "AZURE_CLIENT_ID=b7c2872b-73bf-41ad-8f82-1c9d915e2b29" \
    "AZURE_CLIENT_SECRET=38222242-c19e-434d-a81b-8c157284676d" \
    "TEAMS_APP_PASSWORD=<INSERT_CLIENT_SECRET_VALUE>" \
    "SP_LIBRARY_CORPORATE=21c97509-9081-4c80-b2b8-caeb8d6f58b5" \
    "SP_LIBRARY_AVIATION=1b23062c-ba3a-4b7c-a8b2-118c626ef228" \
    "SP_LIBRARY_INDUSTRIAL=9231ba2e-7a0e-4181-8ef3-9f552ff7ee5a" \
    "SP_LIBRARY_MARITIME=2a08f08b-b6f8-456c-abc3-d9a93b286064" \
    "SP_LIBRARY_RETAIL=b1b125ab-2394-44ee-86cd-56c7f8dc74fd" \
  --query "properties.latestRevisionName" -o tsv

echo "==> Fixing webhook env vars..."
az containerapp update \
  --name certisjbs-webhook \
  --resource-group $RG \
  --set-env-vars "TEAMS_APP_ID=9e8400c3-7360-4475-8688-0538233df529" \
  --query "properties.latestRevisionName" -o tsv

echo "Done."
