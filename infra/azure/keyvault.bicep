// PR Guardian — Azure Key Vault

param prefix string
param location string

@secure()
param anthropicApiKey string
@secure()
param githubToken string
@secure()
param githubWebhookSecret string
@secure()
param dbPassword string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource secretAnthropicKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(anthropicApiKey)) {
  parent: keyVault
  name: 'anthropic-api-key'
  properties: {
    value: anthropicApiKey
  }
}

resource secretGithubToken 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(githubToken)) {
  parent: keyVault
  name: 'github-token'
  properties: {
    value: githubToken
  }
}

resource secretGithubWebhookSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(githubWebhookSecret)) {
  parent: keyVault
  name: 'github-webhook-secret'
  properties: {
    value: githubWebhookSecret
  }
}

resource secretDbPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'db-password'
  properties: {
    value: dbPassword
  }
}

output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
