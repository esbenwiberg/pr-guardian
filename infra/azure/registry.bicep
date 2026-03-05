// PR Guardian — Azure Container Registry

param name string
param location string

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

output loginServer string = registry.properties.loginServer
output registryId string = registry.id
