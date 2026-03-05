// PR Guardian — Azure Infrastructure
// Deploy: az deployment group create -g <rg> -f main.bicep -p envName=prod

targetScope = 'resourceGroup'

@description('Environment name (dev, staging, prod)')
param envName string = 'prod'

@description('Azure region')
param location string = resourceGroup().location

@description('Container image tag')
param imageTag string = 'latest'

@secure()
@description('PostgreSQL admin password')
param dbPassword string

@secure()
@description('Anthropic API key')
param anthropicApiKey string = ''

@secure()
@description('GitHub token for API access')
param githubToken string = ''

@secure()
@description('GitHub webhook secret')
param githubWebhookSecret string = ''

var prefix = 'prguardian-${envName}'
var registryName = replace('prguardian${envName}acr', '-', '')

// Container Registry
module registry 'registry.bicep' = {
  name: 'registry'
  params: {
    name: registryName
    location: location
  }
}

// PostgreSQL Flexible Server
module database 'database.bicep' = {
  name: 'database'
  params: {
    prefix: prefix
    location: location
    adminPassword: dbPassword
  }
}

// Key Vault for secrets
module keyvault 'keyvault.bicep' = {
  name: 'keyvault'
  params: {
    prefix: prefix
    location: location
    anthropicApiKey: anthropicApiKey
    githubToken: githubToken
    githubWebhookSecret: githubWebhookSecret
    dbPassword: dbPassword
  }
}

// Container App Environment + App
module containerApp 'container-app.bicep' = {
  name: 'container-app'
  params: {
    prefix: prefix
    location: location
    registryLoginServer: registry.outputs.loginServer
    registryName: registryName
    imageTag: imageTag
    databaseUrl: database.outputs.connectionString
    keyVaultName: keyvault.outputs.keyVaultName
  }
}

output containerAppUrl string = containerApp.outputs.fqdn
output registryLoginServer string = registry.outputs.loginServer
