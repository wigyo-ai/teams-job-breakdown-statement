// =============================================================================
// Certis JBS Platform — Azure Bicep Template
//
// Provisions the full Azure infrastructure for the JBS Automation Platform:
//   - Azure Container Registry (ACR)
//   - Azure Storage Account + Blob container (document storage)
//   - Azure Key Vault (secrets management)
//   - Log Analytics Workspace (required by Container Apps)
//   - Azure Container Apps Environment
//   - Webhook Container App (external HTTPS ingress)
//   - Orchestrator Container App (internal ingress)
//   - Document Generator Container App (internal ingress)
//   - SharePoint Sync Scheduled Job
//
// Usage:
//   az deployment group create \
//     --resource-group certis-jbs-rg \
//     --template-file deploy/azure/main.bicep \
//     --parameters @deploy/azure/parameters.json
//
// See deploy/azure/README.md for full setup instructions.
// =============================================================================

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short prefix used for all resource names (3-8 alphanumeric characters)')
@minLength(3)
@maxLength(8)
param prefix string = 'certisjbs'

@description('Container image tag to deploy')
param imageTag string = '1.0.0'

@description('Azure Container Registry login server (e.g. certisjbsacr.azurecr.io)')
param acrLoginServer string

@description('ACR admin username')
param acrUsername string

@description('ACR admin password')
@secure()
param acrPassword string

@description('Azure Bot App Registration client ID (TEAMS_APP_ID)')
param teamsAppId string

@description('Azure Bot App Registration client secret (TEAMS_APP_PASSWORD)')
@secure()
param teamsAppPassword string

@description('h2oGPTe API key')
@secure()
param h2ogpteApiKey string

@description('h2oGPTe instance URL')
param h2ogpteAddress string

@description('Azure AD tenant ID (for SharePoint)')
param azureTenantId string = tenant().tenantId

@description('SharePoint app registration client ID')
param azureClientId string

@description('SharePoint app registration client secret')
@secure()
param azureClientSecret string

@description('SharePoint site URL')
param spSiteUrl string

@description('SharePoint library IDs per site category')
param spLibraryCorporate string = ''
param spLibraryAviation string = ''
param spLibraryIndustrial string = ''
param spLibraryMaritime string = ''
param spLibraryRetail string = ''

@description('Tenant identifier used in session state')
param tenantId string = 'certis'

// =============================================================================
// Log Analytics Workspace (required by Container Apps Environment)
// =============================================================================

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// =============================================================================
// Azure Blob Storage
// =============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${prefix}storage'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'certis-jbs-documents'
  properties: {
    publicAccess: 'None'
  }
}

// =============================================================================
// Azure Key Vault
// =============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: '${prefix}-kv'
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource kvSecretTeamsPassword 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'teams-app-password'
  properties: {
    value: teamsAppPassword
  }
}

resource kvSecretH2oApiKey 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'h2ogpte-api-key'
  properties: {
    value: h2ogpteApiKey
  }
}

resource kvSecretAzureClientSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'azure-client-secret'
  properties: {
    value: azureClientSecret
  }
}

resource kvSecretStorageKey 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  parent: keyVault
  name: 'storage-key'
  properties: {
    value: storageAccount.listKeys().keys[0].value
  }
}

// =============================================================================
// Azure Container Apps Environment
// =============================================================================

resource acaEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// =============================================================================
// Webhook Container App — External HTTPS ingress
// =============================================================================

resource webhookApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${prefix}-webhook'
  location: location
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'teams-app-password'
          value: teamsAppPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'jbs-webhook'
          image: '${acrLoginServer}/jbs-webhook:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'TEAMS_APP_ID', value: teamsAppId }
            { name: 'TEAMS_APP_PASSWORD', secretRef: 'teams-app-password' }
            { name: 'ORCHESTRATOR_URL', value: 'http://${prefix}-orchestrator' }
          ]
        }
      ]
      scale: {
        minReplicas: 2
        maxReplicas: 10
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

// =============================================================================
// Orchestrator Container App — Internal ingress only
// =============================================================================

resource orchestratorApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${prefix}-orchestrator'
  location: location
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8001
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'h2ogpte-api-key'
          value: h2ogpteApiKey
        }
        {
          name: 'teams-app-password'
          value: teamsAppPassword
        }
        {
          name: 'azure-client-secret'
          value: azureClientSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'jbs-orchestrator'
          image: '${acrLoginServer}/jbs-orchestrator:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'H2OGPTE_ADDRESS', value: h2ogpteAddress }
            { name: 'H2OGPTE_API_KEY', secretRef: 'h2ogpte-api-key' }
            { name: 'AZURE_TENANT_ID', value: azureTenantId }
            { name: 'AZURE_CLIENT_ID', value: azureClientId }
            { name: 'AZURE_CLIENT_SECRET', secretRef: 'azure-client-secret' }
            { name: 'SP_SITE_URL', value: spSiteUrl }
            { name: 'SP_LIBRARY_CORPORATE', value: spLibraryCorporate }
            { name: 'SP_LIBRARY_AVIATION', value: spLibraryAviation }
            { name: 'SP_LIBRARY_INDUSTRIAL', value: spLibraryIndustrial }
            { name: 'SP_LIBRARY_MARITIME', value: spLibraryMaritime }
            { name: 'SP_LIBRARY_RETAIL', value: spLibraryRetail }
            { name: 'TEAMS_APP_ID', value: teamsAppId }
            { name: 'TEAMS_APP_PASSWORD', secretRef: 'teams-app-password' }
            { name: 'TENANT_ID', value: tenantId }
            { name: 'STATE_BACKEND', value: 'sqlite' }
            { name: 'SQLITE_PATH', value: '/data/jbs_sessions.db' }
            { name: 'SESSION_TTL_HOURS', value: '24' }
            { name: 'DOCUMENT_GENERATOR_URL', value: 'http://${prefix}-docgen' }
          ]
        }
      ]
      scale: {
        minReplicas: 2
        maxReplicas: 8
      }
    }
  }
}

// =============================================================================
// Document Generator Container App — Internal ingress only
// =============================================================================

resource docgenApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${prefix}-docgen'
  location: location
  properties: {
    managedEnvironmentId: acaEnvironment.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8002
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'storage-key'
          value: storageAccount.listKeys().keys[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'jbs-docgen'
          image: '${acrLoginServer}/jbs-docgen:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_STORAGE_ACCOUNT', value: storageAccount.name }
            { name: 'AZURE_STORAGE_CONTAINER', value: 'certis-jbs-documents' }
            { name: 'AZURE_STORAGE_KEY', secretRef: 'storage-key' }
            { name: 'BLOB_PREFIX', value: 'jbs-documents/' }
            { name: 'DOC_URL_EXPIRY_SECONDS', value: '900' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 4
      }
    }
  }
}

// =============================================================================
// SharePoint Sync — ACA Scheduled Job
// =============================================================================

resource sharepointSyncJob 'Microsoft.App/jobs@2023-05-01' = {
  name: '${prefix}-sp-sync'
  location: location
  properties: {
    environmentId: acaEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 2 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800
      replicaRetryLimit: 1
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
        {
          name: 'h2ogpte-api-key'
          value: h2ogpteApiKey
        }
        {
          name: 'azure-client-secret'
          value: azureClientSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'sp-sync'
          image: '${acrLoginServer}/jbs-orchestrator:${imageTag}'
          command: ['python', '-m', 'src.rag.sharepoint_sync']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'H2OGPTE_ADDRESS', value: h2ogpteAddress }
            { name: 'H2OGPTE_API_KEY', secretRef: 'h2ogpte-api-key' }
            { name: 'AZURE_TENANT_ID', value: azureTenantId }
            { name: 'AZURE_CLIENT_ID', value: azureClientId }
            { name: 'AZURE_CLIENT_SECRET', secretRef: 'azure-client-secret' }
            { name: 'SP_SITE_URL', value: spSiteUrl }
            { name: 'SP_LIBRARY_CORPORATE', value: spLibraryCorporate }
            { name: 'SP_LIBRARY_AVIATION', value: spLibraryAviation }
            { name: 'SP_LIBRARY_INDUSTRIAL', value: spLibraryIndustrial }
            { name: 'SP_LIBRARY_MARITIME', value: spLibraryMaritime }
            { name: 'SP_LIBRARY_RETAIL', value: spLibraryRetail }
          ]
        }
      ]
    }
  }
}

// =============================================================================
// Outputs
// =============================================================================

output webhookFqdn string = webhookApp.properties.configuration.ingress.fqdn
output webhookMessagingEndpoint string = 'https://${webhookApp.properties.configuration.ingress.fqdn}/webhook/teams'
output storageAccountName string = storageAccount.name
output keyVaultName string = keyVault.name
output acaEnvironmentName string = acaEnvironment.name
