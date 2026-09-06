#!/bin/bash
# SOCRadar Feeds Function App - Azure Setup Script
# Deploys ARM template + repo-based Function App code

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."

ENV_SOCRADAR_API_KEY="${SOCRADAR_API_KEY:-}"
ENV_SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-}"
ENV_RESOURCE_GROUP="${RESOURCE_GROUP:-}"
ENV_WORKSPACE_NAME="${WORKSPACE_NAME:-}"
ENV_LOCATION="${LOCATION:-}"
ENV_INCLUDE_APT_BLOCK_HASH="${INCLUDE_APT_BLOCK_HASH:-}"
ENV_CUSTOM_COLLECTION_IDS="${CUSTOM_COLLECTION_IDS:-}"
ENV_CUSTOM_COLLECTION_NAMES="${CUSTOM_COLLECTION_NAMES:-}"
ENV_POLLING_INTERVAL_MINUTES="${POLLING_INTERVAL_MINUTES:-}"
ENV_ENABLE_FEEDS_TABLE="${ENABLE_FEEDS_TABLE:-}"
ENV_ENABLE_AUDIT_LOGGING="${ENABLE_AUDIT_LOGGING:-}"
ENV_ENABLE_WORKBOOK="${ENABLE_WORKBOOK:-}"
ENV_DEPLOY_NEW_WORKSPACE="${DEPLOY_NEW_WORKSPACE:-}"
ENV_INITIAL_LOOKBACK_DAYS="${INITIAL_LOOKBACK_DAYS:-}"

# Load .env (SOCRadar credentials)
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env" 2>/dev/null
fi

# Load test.config (Azure resources)
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

SOCRADAR_API_KEY="${ENV_SOCRADAR_API_KEY:-${SOCRADAR_API_KEY:-}}"
SUBSCRIPTION_ID="${ENV_SUBSCRIPTION_ID:-${SUBSCRIPTION_ID:-}}"
RESOURCE_GROUP="${ENV_RESOURCE_GROUP:-${RESOURCE_GROUP:-}}"
WORKSPACE_NAME="${ENV_WORKSPACE_NAME:-${WORKSPACE_NAME:-}}"
LOCATION="${ENV_LOCATION:-${LOCATION:-northeurope}}"
INCLUDE_APT_BLOCK_HASH="${ENV_INCLUDE_APT_BLOCK_HASH:-${INCLUDE_APT_BLOCK_HASH:-true}}"
CUSTOM_COLLECTION_IDS="${ENV_CUSTOM_COLLECTION_IDS:-${CUSTOM_COLLECTION_IDS:-}}"
CUSTOM_COLLECTION_NAMES="${ENV_CUSTOM_COLLECTION_NAMES:-${CUSTOM_COLLECTION_NAMES:-}}"
POLLING_INTERVAL_MINUTES="${ENV_POLLING_INTERVAL_MINUTES:-${POLLING_INTERVAL_MINUTES:-60}}"
ENABLE_FEEDS_TABLE="${ENV_ENABLE_FEEDS_TABLE:-${ENABLE_FEEDS_TABLE:-true}}"
ENABLE_AUDIT_LOGGING="${ENV_ENABLE_AUDIT_LOGGING:-${ENABLE_AUDIT_LOGGING:-true}}"
ENABLE_WORKBOOK="${ENV_ENABLE_WORKBOOK:-${ENABLE_WORKBOOK:-true}}"
DEPLOY_NEW_WORKSPACE="${ENV_DEPLOY_NEW_WORKSPACE:-${DEPLOY_NEW_WORKSPACE:-true}}"
INITIAL_LOOKBACK_DAYS="${ENV_INITIAL_LOOKBACK_DAYS:-${INITIAL_LOOKBACK_DAYS:-30}}"

# Validate required vars
if [ -z "$SOCRADAR_API_KEY" ]; then
    echo "ERROR: Missing SOCRADAR_API_KEY - create scripts/.env with SOCRADAR_API_KEY=xxx"
    exit 1
fi

if [ -z "$SUBSCRIPTION_ID" ] || [ -z "$RESOURCE_GROUP" ] || [ -z "$WORKSPACE_NAME" ]; then
    echo "ERROR: Missing SUBSCRIPTION_ID, RESOURCE_GROUP or WORKSPACE_NAME (scripts/test.config or environment)"
    exit 1
fi

TEMPLATE="$REPO_ROOT/azuredeploy.json"
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template not found: $TEMPLATE"
    exit 1
fi

echo "=== SOCRadar Feeds Function App - Setup ==="
echo ""
echo "Configuration:"
echo "  Resource Group:     $RESOURCE_GROUP"
echo "  Workspace:          $WORKSPACE_NAME"
echo "  Location:           $LOCATION"
echo "  Polling:            $POLLING_INTERVAL_MINUTES min"
echo "  APT Block Hash:     $INCLUDE_APT_BLOCK_HASH"
echo "  Feeds Table:        $ENABLE_FEEDS_TABLE"
echo "  Audit Logging:      $ENABLE_AUDIT_LOGGING"
echo ""

# Check login
ACCOUNT=$(az account show --query "user.name" -o tsv 2>/dev/null)
if [ -z "$ACCOUNT" ]; then
    echo "Not logged in. Run: az login --use-device-code"
    exit 1
fi
echo "Logged in as: $ACCOUNT"
echo ""

# Step 1: Deploy ARM template
echo "=== Step 1: Deploying ARM Template ==="
az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$TEMPLATE" \
    --parameters \
        WorkspaceName="$WORKSPACE_NAME" \
        DeployNewWorkspace="$DEPLOY_NEW_WORKSPACE" \
        WorkspaceLocation="$LOCATION" \
        InitialLookbackDays="$INITIAL_LOOKBACK_DAYS" \
        SocradarApiKey="$SOCRADAR_API_KEY" \
        IncludeAPTBlockHash="$INCLUDE_APT_BLOCK_HASH" \
        CustomCollectionIds="$CUSTOM_COLLECTION_IDS" \
        CustomCollectionNames="$CUSTOM_COLLECTION_NAMES" \
        PollingIntervalMinutes="$POLLING_INTERVAL_MINUTES" \
        EnableFeedsTable="$ENABLE_FEEDS_TABLE" \
        EnableAuditLogging="$ENABLE_AUDIT_LOGGING" \
        EnableWorkbook="$ENABLE_WORKBOOK" \
        ${PACKAGE_URI:+PackageUri="$PACKAGE_URI"} \
    -o table

# Get Function App name from deployment output
FUNC_APP_NAME=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "azuredeploy" \
    --query "properties.outputs.functionAppName.value" -o tsv 2>/dev/null)

if [ -z "$FUNC_APP_NAME" ]; then
    # Fallback: find it in the RG
    FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv 2>/dev/null | head -1)
fi

if [ -z "$FUNC_APP_NAME" ]; then
    echo "ERROR: Could not find Function App name"
    exit 1
fi

echo ""
echo "  Function App: $FUNC_APP_NAME"
echo ""

# Step 2: Verify Function App
echo "=== Step 2: Verifying Function App ==="
FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null || echo "NOT_FOUND")
# The app runs as a user-assigned identity; identity.principalId on the site is empty for that kind.
FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv 2>/dev/null || echo "")

echo "  Function App: $FUNC_APP_NAME"
echo "  State:        $FA_STATE"
echo "  Principal:    $FA_PRINCIPAL"
echo ""

# Step 3: Verify role assignments
echo "=== Step 3: Verifying Role Assignments ==="
if [ -n "$FA_PRINCIPAL" ]; then
    SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$SENTINEL_ROLE" ] && echo "  Sentinel Contributor (workspace): OK" || { echo "  Sentinel Contributor (workspace): MISSING"; exit 1; }

    # Without --all the list covers the subscription scope only and misses
    # the storage-account-scoped assignment the template creates.
    STORAGE_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" --all \
        --query "[?roleDefinitionName=='Storage Table Data Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$STORAGE_ROLE" ] && echo "  Storage Table Data Contributor: OK" || { echo "  Storage Table Data Contributor: MISSING"; exit 1; }
else
    echo "  ERROR: managed identity SOCRadar-Feeds-MI not found"; exit 1
fi
echo ""

# Step 4: Verify Storage Account
echo "=== Step 4: Verifying Storage ==="
STORAGE_ACCOUNT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null | head -1)
if [ -n "$STORAGE_ACCOUNT" ]; then
    echo "  Storage Account: $STORAGE_ACCOUNT"
    TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --query "[?name=='FeedState'].name" -o tsv 2>/dev/null || echo "")
    [ -n "$TABLE_EXISTS" ] && echo "  FeedState Table: OK" || { echo "  FeedState Table: MISSING (the template creates it; the deployment did not finish)"; exit 1; }
else
    echo "  WARNING: No storage account found"
fi
echo ""

# Step 5: Wait for role propagation
echo "=== Step 5: Role Propagation (60 seconds) ==="
for i in $(seq 60 -1 1); do
    printf "\r  Waiting: %d seconds remaining..." "$i"
    sleep 1
done
echo ""
echo "  Done"
echo ""

# Summary
echo "=== Setup Complete ==="
echo ""
echo "  Function App:    $FUNC_APP_NAME ($FA_STATE)"
echo "  Storage Account: ${STORAGE_ACCOUNT:-pending}"
echo "  Workspace:       $WORKSPACE_NAME"
echo ""
echo "Next: ./portal_test.sh"
