// PR Guardian — Azure Database for PostgreSQL Flexible Server

param prefix string
param location string

@secure()
param adminPassword string

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${prefix}-pg'
  location: location
  // Matches the deployed server. Not Burstable: a B-series server runs on CPU
  // credits, and once they are exhausted it throttles hard, which is
  // indistinguishable from an application stall while you are debugging one.
  // Re-applying this template with the old B1ms values would silently downgrade
  // production.
  sku: {
    name: 'Standard_D2ds_v5'
    tier: 'GeneralPurpose'
  }
  properties: {
    version: '16'
    administratorLogin: 'guardian'
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: server
  name: 'prguardian'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Allow Azure services to connect
resource firewallRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output connectionString string = 'postgresql://guardian:${adminPassword}@${server.properties.fullyQualifiedDomainName}:5432/prguardian?sslmode=require'
output serverName string = server.name
