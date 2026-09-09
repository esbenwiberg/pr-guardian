// PR Guardian — Azure Container App

param prefix string
param location string
param registryLoginServer string
param registryName string
param imageTag string
param databaseUrl string
param keyVaultName string

@secure()
param anthropicApiKey string = ''

@secure()
param githubWebhookSecret string = ''

@description('Entra ID (Azure AD) application client ID — leave empty to disable auth')
param entraClientId string = ''

@secure()
@description('Entra ID application client secret')
param entraClientSecret string = ''

@description('Entra ID tenant ID')
param entraTenantId string = ''

@description('Public origin Guardian builds deeplinks from (no trailing slash). Leave empty to derive the ingress FQDN. Set to a custom domain if one fronts the app.')
param guardianBaseUrl string = ''

// Log Analytics workspace
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    // Hard ceiling on ingest. Uncapped (`dailyQuotaGb: -1`, the default) a
    // single hot log path is billed without limit: a per-candidate `exc_info`
    // on the readiness reconciler rendered ~380-line Rich tracebacks and took
    // this workspace from 0.16 to 19.8 GB/day, 437 GB and ~kr 7.2k in a month,
    // with nothing to stop it. Steady state after that fix is well under
    // 0.5 GB/day, so 2 GB is ~4x headroom and still turns a regression into a
    // dropped-logs alert instead of an invoice. Raise it deliberately, not
    // reflexively — hitting the cap is the signal something is looping.
    workspaceCapping: {
      dailyQuotaGb: 2
    }
  }
}

// Container App Environment
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
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

// Container Registry reference
resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

// Container App
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-app'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        corsPolicy: {
          allowedOrigins: ['*']
        }
      }
      registries: [
        {
          server: registryLoginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'anthropic-api-key'
          value: anthropicApiKey
        }
        {
          name: 'github-webhook-secret'
          value: githubWebhookSecret
        }
        {
          name: 'microsoft-provider-authentication-secret'
          value: entraClientSecret
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'pr-guardian'
          image: '${registryLoginServer}/pr-guardian:${imageTag}'
          // Matches what is actually deployed. 2 vCPU / 4Gi is the per-replica
          // ceiling for a Consumption-only environment (no workload profiles), so
          // this cannot be raised without building a new environment and migrating
          // the app — the environment type cannot be converted in place. Steady
          // usage is ~0.4Gi; the headroom is for transient sync spikes.
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'ANTHROPIC_API_KEY'
              secretRef: 'anthropic-api-key'
            }
            {
              name: 'GITHUB_WEBHOOK_SECRET'
              secretRef: 'github-webhook-secret'
            }
            {
              // Authoritative public origin for review deeplinks in PR comments.
              // request.base_url is the internal pod host behind Entra/Envoy, and
              // status/reconciler-triggered reviews carry no request at all — so
              // this env var is what makes the deeplink resolve in prod.
              name: 'GUARDIAN_BASE_URL'
              value: empty(guardianBaseUrl) ? 'https://${prefix}-app.${environment.properties.defaultDomain}' : guardianBaseUrl
            }
          ]
          // Every probe sets timeoutSeconds explicitly. Container Apps defaults it
          // to *one second* when omitted, and that default took the service down:
          // /api/health could not answer within 1s while the app was busy, so
          // replicas never went ready (hundreds of readiness timeouts per day),
          // ACA reported "Persistent Failure to start container" and restarted
          // them, and the restart re-triggered the same work. Do not remove these.
          probes: [
            {
              // Startup gates the other two — neither liveness nor readiness runs
              // until this succeeds. The generous budget (30 x 10s) covers
              // migrations and a cold first request without a slow boot ever being
              // misread as a dead container.
              type: 'Startup'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              timeoutSeconds: 10
              failureThreshold: 5
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 10
              failureThreshold: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        // Background loops are leader-elected, so extra replicas add no throughput
        // for sync/reconcile work — they only multiply restart storms and API
        // traffic. Two is enough to keep ingress served through a rolling deploy.
        maxReplicas: 2
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                // Not 3. The dashboard holds an SSE stream open per open tab
                // (/events), so a handful of ordinary users pinned concurrency
                // above a low threshold permanently and scaled the app out to
                // maxReplicas, multiplying the restart storm.
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// Entra ID authentication (Easy Auth) — only deployed when entraClientId is provided
resource authConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (!empty(entraClientId)) {
  parent: containerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      excludedPaths: [
        '/api/health'
        '/api/webhooks/*'
        '/api/agent/*'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'microsoft-provider-authentication-secret'
          openIdIssuer: 'https://sts.windows.net/${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            'api://${entraClientId}'
          ]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}

// Grant Container App access to ACR
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, containerApp.id, 'acrpull')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
