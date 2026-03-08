#!/usr/bin/env bash
# PR Guardian — ACR Build & Deploy (no local Docker required)
#
# Builds the image remotely via ACR Tasks and deploys a new
# Container App revision.
#
# Usage:
#   ./acr-deploy.sh [image-tag]
#
# Defaults:
#   image-tag = git short SHA
#
# Prerequisites:
#   - Azure CLI (az) logged in
#
# Configuration (override via env vars):
#   AZURE_SUBSCRIPTION  — subscription name or ID (default: MSDN_EWI3)
#   AZURE_RG            — resource group           (default: prguardian-dev-rg)
#   ACR_NAME            — ACR registry name        (default: prguardiandevacr)
#   CONTAINER_APP       — container app name       (default: prguardian-dev-app)
#   ACR_IMAGE           — image repository name    (default: pr-guardian)

set -euo pipefail

SUBSCRIPTION="${AZURE_SUBSCRIPTION:-MSDN_EWI3}"
RG="${AZURE_RG:-prguardian-dev-rg}"
ACR="${ACR_NAME:-prguardiandevacr}"
APP="${CONTAINER_APP:-prguardian-dev-app}"
IMAGE="${ACR_IMAGE:-pr-guardian}"
TAG="${1:-$(git rev-parse --short HEAD)}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== PR Guardian — ACR Build & Deploy ==="
echo "Subscription:  $SUBSCRIPTION"
echo "Resource Group: $RG"
echo "Registry:       $ACR"
echo "Container App:  $APP"
echo "Image Tag:      $TAG"
echo ""

# 1. Switch subscription
echo "--- Switching subscription..."
az account set --subscription "$SUBSCRIPTION"
echo "    Active: $(az account show --query name -o tsv)"

# 2. Build image remotely via ACR Tasks (no local Docker needed)
echo "--- Building image via ACR Tasks..."
az acr build \
    --registry "$ACR" \
    --image "$IMAGE:$TAG" \
    --image "$IMAGE:latest" \
    "$PROJECT_ROOT"

# 3. Deploy new revision
FULL_IMAGE="$ACR.azurecr.io/$IMAGE:$TAG"
echo "--- Deploying new revision ($FULL_IMAGE)..."
az containerapp update \
    --name "$APP" \
    --resource-group "$RG" \
    --image "$FULL_IMAGE" \
    --output none

# 4. Verify
echo "--- Verifying deployment..."
REVISION=$(az containerapp revision list \
    --name "$APP" \
    --resource-group "$RG" \
    --query "[?properties.trafficWeight > \`0\`] | [0].name" \
    -o tsv)
FQDN=$(az containerapp show \
    --name "$APP" \
    --resource-group "$RG" \
    --query "properties.configuration.ingress.fqdn" \
    -o tsv)

echo ""
echo "=== Deployment Complete ==="
echo "Revision: $REVISION"
echo "URL:      https://$FQDN"
echo "Health:   https://$FQDN/api/health"
