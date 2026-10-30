#!/usr/bin/env bash
# One-time Azure setup for the Attendance Portal. Run it in Azure Cloud Shell (Bash), signed in to the
# right subscription:   bash infra/azure-setup.sh
#
# Creates: resource group, container registry, Linux App Service plan + Web App (container), Cosmos DB for
# MongoDB, and a GitHub deploy identity (OpenID Connect, no password). Prints the GitHub secrets and
# variables to add at the end. Microsoft sign-in and Mail.Send are set up separately (docs/DEPLOYMENT.md).
#
# Override any of these before running, e.g.  LOCATION=southindia APP=attendance-hr bash infra/azure-setup.sh
set -euo pipefail

LOCATION="${LOCATION:-centralindia}"
PREFIX="${PREFIX:-attendance}"
SUFFIX="${SUFFIX:-$RANDOM}"
RG="${RG:-rg-$PREFIX}"
ACR="${ACR:-${PREFIX}acr${SUFFIX}}"           # letters and digits only, globally unique
PLAN="${PLAN:-plan-$PREFIX}"
PLAN_SKU="${PLAN_SKU:-B1}"
APP="${APP:-$PREFIX-portal-$SUFFIX}"          # becomes https://<APP>.azurewebsites.net
COSMOS="${COSMOS:-$PREFIX-db-$SUFFIX}"
GITHUB_REPO="${GITHUB_REPO:-karthi251104/attendance-project}"

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
APP_URL="https://$APP.azurewebsites.net"
echo "Subscription $SUBSCRIPTION_ID, region $LOCATION, app $APP_URL"

echo "1/6 Resource group and container registry"
az group create -n "$RG" -l "$LOCATION" -o none
az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled false -o none
ACR_ID="$(az acr show -n "$ACR" --query id -o tsv)"

echo "2/6 App Service plan and Web App (container)"
az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku "$PLAN_SKU" -o none
az webapp create -g "$RG" -p "$PLAN" -n "$APP" --container-image-name mcr.microsoft.com/appsvc/staticsite:latest -o none
az webapp update -g "$RG" -n "$APP" --https-only true -o none
az webapp config set -g "$RG" -n "$APP" --always-on true --min-tls-version 1.2 --ftps-state Disabled \
  --generic-configurations '{"healthCheckPath": "/healthz", "acrUseManagedIdentityCreds": true}' -o none
# The Web App pulls images from the registry with its own managed identity (no registry password).
PRINCIPAL_ID="$(az webapp identity assign -g "$RG" -n "$APP" --query principalId -o tsv)"
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$ACR_ID" -o none
WEBAPP_ID="$(az webapp show -g "$RG" -n "$APP" --query id -o tsv)"

echo "3/6 Cosmos DB for MongoDB (this step takes a few minutes)"
az cosmosdb create -g "$RG" -n "$COSMOS" --kind MongoDB --server-version 4.2 \
  --locations regionName="$LOCATION" --backup-policy-type Continuous -o none
MONGODB_URI="$(az cosmosdb keys list -g "$RG" -n "$COSMOS" --type connection-strings \
  --query "connectionStrings[0].connectionString" -o tsv)"

echo "4/6 App settings (secrets stay in Azure, never in GitHub)"
az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
  ENV=production \
  WEBSITES_PORT=8000 \
  APP_BASE_URL="$APP_URL" \
  SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n=+/')" \
  MONGODB_URI="$MONGODB_URI" \
  MONGODB_DB=attendance \
  DEV_LOGIN=false \
  TIMEZONE=Asia/Kolkata \
  EMAIL_MODE=console

echo "5/6 GitHub deploy identity (OpenID Connect)"
DEPLOY_APP_ID="$(az ad app create --display-name "$PREFIX-github-deploy" --query appId -o tsv)"
az ad sp create --id "$DEPLOY_APP_ID" -o none 2>/dev/null || true
az ad app federated-credential create --id "$DEPLOY_APP_ID" --parameters "{
  \"name\": \"github-production\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:$GITHUB_REPO:environment:production\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}" -o none
sleep 20 # let the new service principal replicate before assigning roles
az role assignment create --assignee "$DEPLOY_APP_ID" --role AcrPush --scope "$ACR_ID" -o none
az role assignment create --assignee "$DEPLOY_APP_ID" --role "Website Contributor" --scope "$WEBAPP_ID" -o none

echo "6/6 Done."
cat <<EOF

Add these in GitHub: repo $GITHUB_REPO > Settings > Secrets and variables > Actions
  Secrets:
    AZURE_CLIENT_ID        $DEPLOY_APP_ID
    AZURE_TENANT_ID        $TENANT_ID
    AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION_ID
  Variables:
    AZURE_WEBAPP_NAME      $APP
    ACR_NAME               $ACR
    APP_URL                $APP_URL
And create the environment "production" (Settings > Environments), optionally with required reviewers.

Then finish the app settings in Azure (Web App > Settings > Environment variables), see docs/DEPLOYMENT.md:
  HR_ADMIN_EMAILS, ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, SENDER_MAILBOX, EMAIL_MODE=graph
The first deploy starts on the next push to main (or Actions > Deploy > Run workflow).
EOF
