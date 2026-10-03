#!/bin/bash
# SOCRadar Feeds Function App - Azure Setup Script
# Deploys ARM template + repo-based Function App code

# `bash -x` would print the API key assignments; no xtrace in this script.
{ set +x; } 2>/dev/null
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
    # A broken .env must be visible, but bash's message quotes the line (the key): show line numbers only.
    ENVERR=$(mktemp)
    if ! bash -n "$SCRIPT_DIR/.env" 2>"$ENVERR" || ! source "$SCRIPT_DIR/.env" 2>>"$ENVERR"; then
        echo "ERROR: CANNOT MEASURE: .env did not load (line: $(grep -o 'line [0-9]*' "$ENVERR" | sort -u | tr '\n' ' ')); values not shown"
        rm -f "$ENVERR"
        exit 1
    fi
    rm -f "$ENVERR"
fi

# Load test.config (Azure resources)
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

{ set +x; } 2>/dev/null
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
if ! ACCOUNT=$(az account show --query "user.name" -o tsv) || [ -z "$ACCOUNT" ]; then
    echo "Not logged in. Run: az login --use-device-code"
    exit 1
fi
echo "Logged in as: $ACCOUNT"
if ! ACTIVE_SUB=$(az account show --query id -o tsv); then
    echo "ERROR: CANNOT MEASURE: az account show failed"
    exit 1
fi
if [ "$ACTIVE_SUB" != "$SUBSCRIPTION_ID" ]; then
    echo "ERROR: az is on subscription '${ACTIVE_SUB:-none}' but SUBSCRIPTION_ID is '$SUBSCRIPTION_ID'"
    exit 1
fi
echo ""

# The API key and the PackageUri (it carries a SAS) go in a 0600 file, not on the
# az command line where ps shows them. Removed on exit.
umask 077
SECRETS_FILE=$(mktemp)
trap 'rm -f "$SECRETS_FILE"' EXIT
{ set +x; } 2>/dev/null
SK="$SOCRADAR_API_KEY" PU="${PACKAGE_URI:-}" python3 -c '
import json, os
p = {"SocradarApiKey": {"value": os.environ["SK"]}}
if os.environ["PU"]:
    p["PackageUri"] = {"value": os.environ["PU"]}
print(json.dumps({"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                  "contentVersion": "1.0.0.0", "parameters": p}))' > "$SECRETS_FILE"

# Step 1: Deploy ARM template
echo "=== Step 1: Deploying ARM Template ==="
az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$TEMPLATE" \
    --parameters @"$SECRETS_FILE" \
        WorkspaceName="$WORKSPACE_NAME" \
        DeployNewWorkspace="$DEPLOY_NEW_WORKSPACE" \
        WorkspaceLocation="$LOCATION" \
        InitialLookbackDays="$INITIAL_LOOKBACK_DAYS" \
        IncludeAPTBlockHash="$INCLUDE_APT_BLOCK_HASH" \
        CustomCollectionIds="$CUSTOM_COLLECTION_IDS" \
        CustomCollectionNames="$CUSTOM_COLLECTION_NAMES" \
        PollingIntervalMinutes="$POLLING_INTERVAL_MINUTES" \
        EnableFeedsTable="$ENABLE_FEEDS_TABLE" \
        EnableAuditLogging="$ENABLE_AUDIT_LOGGING" \
        EnableWorkbook="$ENABLE_WORKBOOK" \
    -o table

# Get Function App name from deployment output. A failed read is not "absent":
# `if ! X=$(...)` keeps set -e from exiting without a word, and the reason shows.
FUNC_APP_NAME=""
if ! FUNC_APP_NAME=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "azuredeploy" \
    --query "properties.outputs.functionAppName.value" -o tsv); then
    echo "  deployment outputs unreadable (reason above); looking in the resource group"
    FUNC_APP_NAME=""
fi

if [ -z "$FUNC_APP_NAME" ]; then
    # Fallback: find it in the RG
    if ! FA_OUT=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv); then
        echo "ERROR: CANNOT MEASURE: az functionapp list failed"; exit 1
    fi
    FUNC_APP_NAME=${FA_OUT%%$'\n'*}
fi

if [ -z "$FUNC_APP_NAME" ]; then
    echo "ERROR: FAIL (absent): no Function App in $RESOURCE_GROUP"
    exit 1
fi

echo ""
echo "  Function App: $FUNC_APP_NAME"
echo ""

# Step 2: Verify Function App
echo "=== Step 2: Verifying Function App ==="
# az exits 3 for a resource that is not there; any other failure is a look that did not happen.
FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv) && FA_RC=0 || FA_RC=$?
if [ "$FA_RC" -eq 3 ]; then
    echo "ERROR: FAIL (absent): Function App $FUNC_APP_NAME not found"; exit 1
elif [ "$FA_RC" -ne 0 ] || [ -z "$FA_STATE" ]; then
    echo "ERROR: CANNOT MEASURE: az functionapp show failed (rc=$FA_RC)"; exit 1
fi
# The app runs as a user-assigned identity; identity.principalId on the site is empty for that kind.
FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv) && ID_RC=0 || ID_RC=$?
if [ "$ID_RC" -eq 3 ]; then
    echo "ERROR: FAIL (absent): managed identity SOCRadar-Feeds-MI not found"; exit 1
elif [ "$ID_RC" -ne 0 ] || [ -z "$FA_PRINCIPAL" ]; then
    echo "ERROR: CANNOT MEASURE: az identity show failed or returned no principalId (rc=$ID_RC)"; exit 1
fi

echo "  Function App: $FUNC_APP_NAME"
echo "  State:        $FA_STATE"
echo "  Principal:    $FA_PRINCIPAL"
echo ""

# Step 3: Verify role assignments
echo "=== Step 3: Verifying Role Assignments ==="
# The storage role is checked on the storage account itself, not on the whole
# subscription: an assignment on some other account must not count.
if ! SA_OUT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].id" -o tsv); then
    echo "ERROR: CANNOT MEASURE: az storage account list failed (reason above)"; exit 1
fi
STORAGE_ID=${SA_OUT%%$'\n'*}
if [ -z "$STORAGE_ID" ]; then
    echo "ERROR: FAIL (absent): no storage account srfeeds* in $RESOURCE_GROUP (the template creates it)"; exit 1
fi
STORAGE_ACCOUNT=${STORAGE_ID##*/}

if ! SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
    --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \
    --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv); then
    echo "ERROR: CANNOT MEASURE: az role assignment list failed (workspace scope)"; exit 1
fi
[ -n "$SENTINEL_ROLE" ] && echo "  Sentinel Contributor (workspace): OK" || { echo "  Sentinel Contributor (workspace): MISSING"; exit 1; }

if ! STORAGE_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" --scope "$STORAGE_ID" \
    --query "[?roleDefinitionName=='Storage Table Data Contributor'].roleDefinitionName" -o tsv); then
    echo "ERROR: CANNOT MEASURE: az role assignment list failed (storage scope)"; exit 1
fi
[ -n "$STORAGE_ROLE" ] && echo "  Storage Table Data Contributor: OK" || { echo "  Storage Table Data Contributor: MISSING"; exit 1; }
echo ""

# Step 4: Verify Storage Account
echo "=== Step 4: Verifying Storage ==="
echo "  Storage Account: $STORAGE_ACCOUNT"
# --auth-mode key: az writes a WARNING to stderr on every such call; stdout is the answer.
if ! TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key --query "[?name=='FeedState'].name" -o tsv); then
    echo "  FeedState Table: CANNOT MEASURE (az storage table list failed)"; exit 1
fi
[ -n "$TABLE_EXISTS" ] && echo "  FeedState Table: OK" || { echo "  FeedState Table: MISSING (the template creates it; the deployment did not finish)"; exit 1; }
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
echo "  Storage Account: $STORAGE_ACCOUNT"
echo "  Workspace:       $WORKSPACE_NAME"
echo ""
echo "Next: ./portal_test.sh"
