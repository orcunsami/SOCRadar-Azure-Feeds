#!/bin/bash
# SOCRadar Feeds Function App - Azure Setup Script
# Deploys ARM template + zip deploys Function App code

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."

# Load .env (SOCRadar credentials)
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env" 2>/dev/null
fi

# Load test.config (Azure resources)
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

# Validate required vars
if [ -z "$SOCRADAR_API_KEY" ]; then
    echo "ERROR: Missing SOCRADAR_API_KEY - create scripts/.env with SOCRADAR_API_KEY=xxx"
    exit 1
fi

if [ -z "$SUBSCRIPTION_ID" ] || [ -z "$RESOURCE_GROUP" ] || [ -z "$WORKSPACE_NAME" ]; then
    echo "ERROR: Missing test.config values"
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
echo "  Location:           ${LOCATION:-northeurope}"
echo "  Polling:            ${POLLING_INTERVAL_MINUTES:-60} min"
echo "  APT Block Hash:     ${INCLUDE_APT_BLOCK_HASH:-true}"
echo "  Feeds Table:        ${ENABLE_FEEDS_TABLE:-true}"
echo "  Audit Logging:      ${ENABLE_AUDIT_LOGGING:-true}"
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
        WorkspaceLocation="${LOCATION:-northeurope}" \
        SocradarApiKey="$SOCRADAR_API_KEY" \
        IncludeAPTBlockHash="${INCLUDE_APT_BLOCK_HASH:-true}" \
        CustomCollectionIds="${CUSTOM_COLLECTION_IDS:-}" \
        CustomCollectionNames="${CUSTOM_COLLECTION_NAMES:-}" \
        PollingIntervalMinutes="${POLLING_INTERVAL_MINUTES:-60}" \
        EnableFeedsTable="${ENABLE_FEEDS_TABLE:-true}" \
        EnableAuditLogging="${ENABLE_AUDIT_LOGGING:-true}" \
        EnableWorkbook="${ENABLE_WORKBOOK:-true}" \
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

# Step 2: Zip deploy Function code
echo "=== Step 2: Deploying Function Code ==="
FUNC_DIR="$REPO_ROOT/FunctionApp"
if [ ! -f "$FUNC_DIR/function_app.py" ]; then
    echo "ERROR: FunctionApp/function_app.py not found"
    exit 1
fi

# Create zip of FunctionApp contents
ZIP_FILE="/tmp/socradar-feeds-func.zip"
rm -f "$ZIP_FILE"
(cd "$FUNC_DIR" && zip -r "$ZIP_FILE" . -x "*.pyc" "__pycache__/*" ".venv/*" "local.settings.json" "local.settings.json.example")
echo "  Created zip: $(du -h "$ZIP_FILE" | cut -f1)"

# Deploy with remote build (installs requirements.txt on Azure)
echo "  Deploying (remote build)..."
az functionapp deployment source config-zip \
    --name "$FUNC_APP_NAME" \
    -g "$RESOURCE_GROUP" \
    --src "$ZIP_FILE" \
    --build-remote true \
    -o none

rm -f "$ZIP_FILE"
echo "  Deploy complete"
echo ""

# Step 3: Verify Function App
echo "=== Step 3: Verifying Function App ==="
FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null || echo "NOT_FOUND")
FA_PRINCIPAL=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "identity.principalId" -o tsv 2>/dev/null || echo "")

echo "  Function App: $FUNC_APP_NAME"
echo "  State:        $FA_STATE"
echo "  Principal:    $FA_PRINCIPAL"
echo ""

# Step 4: Verify role assignments
echo "=== Step 4: Verifying Role Assignments ==="
if [ -n "$FA_PRINCIPAL" ]; then
    SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$SENTINEL_ROLE" ] && echo "  Sentinel Contributor: OK" || echo "  Sentinel Contributor: MISSING"

    STORAGE_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --query "[?roleDefinitionName=='Storage Table Data Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$STORAGE_ROLE" ] && echo "  Storage Table Data Contributor: OK" || echo "  Storage Table Data Contributor: MISSING"
else
    echo "  WARNING: No principal ID - cannot verify roles"
fi
echo ""

# Step 5: Verify Storage Account
echo "=== Step 5: Verifying Storage ==="
STORAGE_ACCOUNT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null | head -1)
if [ -n "$STORAGE_ACCOUNT" ]; then
    echo "  Storage Account: $STORAGE_ACCOUNT"
    TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --query "[?name=='FeedState'].name" -o tsv 2>/dev/null || echo "")
    [ -n "$TABLE_EXISTS" ] && echo "  FeedState Table: OK" || echo "  FeedState Table: will be created on first run"
else
    echo "  WARNING: No storage account found"
fi
echo ""

# Step 6: Wait for role propagation
echo "=== Step 6: Role Propagation (60 seconds) ==="
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
