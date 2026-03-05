#!/usr/bin/env bash
# PR Guardian — Azure Deployment Script
#
# Usage:
#   ./deploy.sh <resource-group> <env-name> [image-tag]
#
# Prerequisites:
#   - Azure CLI (az) logged in
#   - Docker logged into ACR
#
# Example:
#   ./deploy.sh rg-prguardian prod latest

set -euo pipefail

RG="${1:?Usage: deploy.sh <resource-group> <env-name> [image-tag]}"
ENV_NAME="${2:?Usage: deploy.sh <resource-group> <env-name> [image-tag]}"
IMAGE_TAG="${3:-latest}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== PR Guardian Azure Deployment ==="
echo "Resource Group: $RG"
echo "Environment:    $ENV_NAME"
echo "Image Tag:      $IMAGE_TAG"
echo ""

# Prompt for secrets if not set
if [ -z "${DB_PASSWORD:-}" ]; then
    read -s -p "PostgreSQL admin password: " DB_PASSWORD
    echo ""
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    read -s -p "Anthropic API key (or press Enter to skip): " ANTHROPIC_API_KEY
    echo ""
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
    read -s -p "GitHub token (or press Enter to skip): " GITHUB_TOKEN
    echo ""
fi

if [ -z "${GITHUB_WEBHOOK_SECRET:-}" ]; then
    read -s -p "GitHub webhook secret (or press Enter to skip): " GITHUB_WEBHOOK_SECRET
    echo ""
fi

# Create resource group if it doesn't exist
echo "--- Ensuring resource group exists..."
az group create --name "$RG" --location westeurope --output none 2>/dev/null || true

# Deploy infrastructure
echo "--- Deploying infrastructure (Bicep)..."
DEPLOY_OUTPUT=$(az deployment group create \
    --resource-group "$RG" \
    --template-file "$SCRIPT_DIR/main.bicep" \
    --parameters \
        envName="$ENV_NAME" \
        imageTag="$IMAGE_TAG" \
        dbPassword="$DB_PASSWORD" \
        anthropicApiKey="${ANTHROPIC_API_KEY:-}" \
        githubToken="${GITHUB_TOKEN:-}" \
        githubWebhookSecret="${GITHUB_WEBHOOK_SECRET:-}" \
    --output json)

REGISTRY=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.registryLoginServer.value')
APP_URL=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.containerAppUrl.value')

echo "--- Infrastructure deployed."
echo "    Registry: $REGISTRY"
echo "    App URL:  https://$APP_URL"

# Build and push Docker image
echo "--- Building Docker image..."
az acr login --name "${REGISTRY%%.*}"

docker build -t "$REGISTRY/pr-guardian:$IMAGE_TAG" "$PROJECT_ROOT"
docker push "$REGISTRY/pr-guardian:$IMAGE_TAG"

echo ""
echo "=== Deployment Complete ==="
echo "Service URL: https://$APP_URL"
echo "Health:      https://$APP_URL/api/health"
echo "Webhooks:"
echo "  GitHub:    https://$APP_URL/api/webhooks/github"
echo "  ADO:       https://$APP_URL/api/webhooks/ado"
echo ""
echo "Next steps:"
echo "  1. Configure GitHub webhook → https://$APP_URL/api/webhooks/github"
echo "  2. Set webhook secret to match GITHUB_WEBHOOK_SECRET"
echo "  3. Select events: Pull requests"
