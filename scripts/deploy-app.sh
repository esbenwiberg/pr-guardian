#!/usr/bin/env bash
# PR Guardian — Deploy via Orcha
#
# Builds the image remotely via ACR Tasks and deploys a new
# Container App revision. Designed to run from Orcha's deploy
# shell where env vars are injected automatically.
#
# Required env vars (set in Orcha deploy config):
#   AZURE_SUBSCRIPTION  — Azure subscription name or ID
#   AZURE_RG            — Resource group
#   ACR_NAME            — ACR registry name
#   CONTAINER_APP       — Container App name
#
# Optional:
#   ACR_IMAGE           — Image repository name (default: pr-guardian)

set -euo pipefail

# --- Validate required env vars ---
missing=()
for var in AZURE_SUBSCRIPTION AZURE_RG ACR_NAME CONTAINER_APP; do
  [[ -z "${!var:-}" ]] && missing+=("$var")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required env vars: ${missing[*]}" >&2
  echo "Set these in Orcha's Deploy Environment Variables." >&2
  exit 1
fi

IMAGE="${ACR_IMAGE:-pr-guardian}"
TAG="$(git rev-parse --short HEAD)"
FULL_IMAGE="$ACR_NAME.azurecr.io/$IMAGE:$TAG"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== PR Guardian Deploy ==="
echo "Image: $FULL_IMAGE"
echo ""

# 1. Switch subscription
az account set --subscription "$AZURE_SUBSCRIPTION"

# 2. Build image remotely via ACR Tasks
echo "--- Building image via ACR Tasks..."
az acr build \
    --registry "$ACR_NAME" \
    --image "$IMAGE:$TAG" \
    --image "$IMAGE:latest" \
    "$PROJECT_ROOT"

# 3. Deploy new revision
echo "--- Deploying revision..."
az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$AZURE_RG" \
    --image "$FULL_IMAGE" \
    --output none

# 4. Verify
FQDN=$(az containerapp show \
    --name "$CONTAINER_APP" \
    --resource-group "$AZURE_RG" \
    --query "properties.configuration.ingress.fqdn" \
    -o tsv)

echo ""
echo "=== Deployed ==="
echo "URL:    https://$FQDN"
echo "Health: https://$FQDN/api/health"
