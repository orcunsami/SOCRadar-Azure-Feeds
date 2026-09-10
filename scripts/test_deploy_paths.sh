#!/usr/bin/env bash
# Deployment paths a customer can actually take, run against live Azure.
#
# Two defects shipped because neither of these paths was ever run:
#
#   EXP-AZURE-0198  A wrong WorkspaceName with the parameters left at their
#                   defaults half-installed. ARM counts a resource whose
#                   `condition` is false as a satisfied dependency, so depending
#                   on the workspace stopped nothing: the storage account, the
#                   identity and the App Service Plan were created and only then
#                   did it fail. Every greenfield E2E ran DeployNewWorkspace=true,
#                   so the defaults -- what a one-click deploy keeps -- were never
#                   exercised.
#
#   EXP-AZURE-0202  Azure stopped accepting the CREATE of a Linux consumption
#                   Function App whose WEBSITE_RUN_FROM_PACKAGE is a redirecting
#                   URL. The same release URL was accepted at 15:01 and rejected
#                   at 15:10 on 7 Sep 2026. The package now arrives by zip deploy
#                   from the deploymentScript.
#
# Four paths:
#   A  missing workspace, DeployNewWorkspace=false -> fails, resource group stays EMPTY
#   B  defaults (one click, new name)       -> succeeds, guard SKIPPED, functions INDEXED
#   C  existing workspace, DeployNewWorkspace=false -> guard SUCCEEDS and resolves customerId
#   D  redeploy of B                      -> succeeds AND the app still has functions
#
# B and D assert three things, none of them the provisioning state: the function
# count, the shape of the package pointer, and -- for B -- that the app still
# answers after a restart. A green deployment with zero indexed functions is one
# failure this template can produce (a redeploy resets WEBSITE_RUN_FROM_PACKAGE
# to "1" and without forceUpdateTag would not re-run the push). The other is
# worse and the count cannot see it: with the pointer left at "1" the package is
# staged where the host cannot reload it, so ARM keeps reporting a function while
# the host answers 503 from the next restart onwards. Asserting Succeeded, or
# even the count alone, would pass both.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$REPO_ROOT/azuredeploy.json"

LOCATION="${TEST_LOCATION:-northeurope}"
API_KEY="${TEST_SOCRADAR_API_KEY:-placeholder}"
KEEP="${KEEP_RESOURCES:-false}"

SFX=$(python3 -c "import uuid;print(uuid.uuid4().hex[:4])")
RG_APP="rg-feeds-paths-$SFX"
RG_C="rg-feeds-paths-c-$SFX"
WS="ws-feeds-paths-$SFX"
MISSING="ws-does-not-exist-$SFX"

fails=0
row() {  # row <name> <PASS|FAIL> <detail>
    printf '%-40s %-5s %s\n' "$1" "$2" "$3"
    [ "$2" = PASS ] || fails=$((fails + 1))
}

cleanup() {
    if [ "$KEEP" = true ]; then
        echo "KEEP_RESOURCES=true, leaving $RG_APP and $RG_C in place"
        return
    fi
    for rg in "$RG_APP" "$RG_C"; do
        az group delete -n "$rg" --yes --no-wait -o none 2>/dev/null
    done
    echo "cleanup: delete queued for $RG_APP and $RG_C"
}
trap cleanup EXIT

deploy() {  # deploy <rg> <name> <extra params...>
    local rg="$1" name="$2"; shift 2
    az deployment group create -g "$rg" -n "$name" --template-file "$TEMPLATE" \
        --parameters SocradarApiKey="$API_KEY" WorkspaceLocation="$LOCATION" "$@" \
        -o none 2>/dev/null
}

# The only signal that separates a loaded package from a Running empty app.
# Read through ARM rather than `az functionapp function list`: that command
# returned an empty string right after a deployment while the ARM call already
# reported 1, and an empty read is indistinguishable from a real zero.
function_count() {  # function_count <rg>
    local rg="$1" app sub
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv 2>/dev/null)
    [ -n "$app" ] || { echo "no-app"; return; }
    sub=$(az account show --query id -o tsv 2>/dev/null)
    for _ in $(seq 1 6); do
        n=$(az rest --method get --url \
            "https://management.azure.com/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.Web/sites/$app/functions?api-version=2023-12-01" \
            --query "length(value)" -o tsv 2>/dev/null)
        case "$n" in ''|*[!0-9]*) ;; *) [ "$n" -ge 1 ] && { echo "$n"; return; };; esac
        python3 -c "import time;time.sleep(15)"
    done
    echo "${n:-0}"
}

# The count is ARM metadata and it lies on its own: on a rig app whose pointer
# was left at "1", ARM kept reporting 1 function while the host answered 503 for
# three and a half minutes. The pointer's SHAPE is the second reading -- the
# package has to sit in the function-releases container the app reloads from.
# The value itself is never printed: it carries a SAS token.
pointer_shape() {  # pointer_shape <rg>
    local rg="$1" app v
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv 2>/dev/null)
    [ -n "$app" ] || { echo "no-app"; return; }
    v=$(az functionapp config appsettings list -g "$rg" -n "$app" \
        --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value" -o tsv 2>/dev/null)
    case "$v" in
        *function-releases*) echo blob ;;
        1) echo one ;;
        "") echo missing ;;
        *) echo other ;;
    esac
}

# Orcun's failure was "you said it works and it broke". A package the host
# cannot reload survives until the first restart, so restart it here.
survives_restart() {  # survives_restart <rg>
    local rg="$1" app code
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv 2>/dev/null)
    [ -n "$app" ] || { echo "no-app"; return; }
    az functionapp restart -g "$rg" -n "$app" -o none 2>/dev/null
    for _ in $(seq 1 12); do
        python3 -c "import time;time.sleep(15)"
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$app.azurewebsites.net/")
        [ "$code" = 200 ] && { echo 200; return; }
    done
    echo "${code:-no-answer}"
}

# Which step failed, read from the deployment. Never inferred from the state:
# a Function App failure once got reported as "the guard blocks a workspace".
failed_step() {  # failed_step <rg> <deployment>
    az deployment operation group list -g "$1" -n "$2" \
        --query "[?properties.provisioningState=='Failed'].properties.targetResource.resourceName" \
        -o tsv 2>/dev/null | sort -u | tr '\n' ' '
}

echo "[1/5] Creating $RG_APP and $RG_C ..."
az group create -n "$RG_APP" -l "$LOCATION" -o none
az group create -n "$RG_C"   -l "$LOCATION" -o none

# --- A: the customer's typo, defaults left alone --------------------------------------
echo "[2/5] Path A: missing workspace with DeployNewWorkspace=false ..."
deploy "$RG_APP" path-a WorkspaceName="$MISSING" DeployNewWorkspace=false
state=$(az deployment group show -g "$RG_APP" -n path-a --query properties.provisioningState -o tsv 2>/dev/null)
left=$(az resource list -g "$RG_APP" --query "length(@)" -o tsv 2>/dev/null)
steps=$(failed_step "$RG_APP" path-a)

[ "$state" = Failed ] \
    && row "A deployment fails" PASS "$state" \
    || row "A deployment fails" FAIL "expected Failed, got '${state:-<unreadable>}'"
[ "${left:-x}" = 0 ] \
    && row "A leaves nothing behind" PASS "0 resources" \
    || row "A leaves nothing behind" FAIL "expected 0, found '${left:-<unreadable>}' - the guard did not hold"
printf '%s' "$steps" | grep -q 'precheck-workspace-exists' \
    && row "A blames the precheck" PASS "precheck-workspace-exists" \
    || row "A blames the precheck" FAIL "failed step(s) were: ${steps:-<none read>}"

# --- B: greenfield, and the package has to actually load -------------------------------
echo "[3/5] Path B: new workspace name with the parameters at their defaults ..."
deploy "$RG_APP" path-b WorkspaceName="$WS"
state=$(az deployment group show -g "$RG_APP" -n path-b --query properties.provisioningState -o tsv 2>/dev/null)
steps=$(failed_step "$RG_APP" path-b)
[ "$state" = Succeeded ] \
    && row "B greenfield succeeds" PASS "$state" \
    || row "B greenfield succeeds" FAIL "expected Succeeded, got '${state:-<unreadable>}', failed step(s): ${steps:-<none>}"
guard_ran=$(az deployment operation group list -g "$RG_APP" -n path-b \
    --query "[?properties.targetResource.resourceName=='precheck-workspace-exists'] | length(@)" -o tsv 2>/dev/null)
[ "${guard_ran:-x}" = 0 ] \
    && row "B skips the precheck" PASS "condition false" \
    || row "B skips the precheck" FAIL "the precheck ran against a workspace being created"
n=$(function_count "$RG_APP")
[ "${n:-0}" -ge 1 ] 2>/dev/null \
    && row "B indexes the function" PASS "$n function(s)" \
    || row "B indexes the function" FAIL "function count was '${n:-<unreadable>}' with the pointer '$(pointer_shape "$RG_APP")' - a blob pointer here means the package arrived and only the indexing was slower than the script's window"
s=$(pointer_shape "$RG_APP")
[ "$s" = blob ] \
    && row "B stores the package where the app reloads it" PASS "pointer is a function-releases blob" \
    || row "B stores the package where the app reloads it" FAIL "pointer shape is '$s' - with 'one' the host answers 503 after a restart while ARM still reports a function"
s=$(survives_restart "$RG_APP")
[ "$s" = 200 ] \
    && row "B still serves after a restart" PASS "host answered 200" \
    || row "B still serves after a restart" FAIL "host answered '$s' - the install works until the first restart"

# --- C: the guard's success branch, which B never exercises ----------------------------
echo "[4/5] Path C: existing workspace, DeployNewWorkspace=false ..."
az monitor log-analytics workspace create -g "$RG_C" -n "$WS" -l "$LOCATION" --retention-time 30 -o none 2>/dev/null
deploy "$RG_C" path-c WorkspaceName="$WS" DeployNewWorkspace=false
state=$(az deployment group show -g "$RG_C" -n path-c --query properties.provisioningState -o tsv 2>/dev/null)
steps=$(failed_step "$RG_C" path-c)
guard_state=$(az deployment operation group list -g "$RG_C" -n path-c \
    --query "[?properties.targetResource.resourceName=='precheck-workspace-exists'].properties.provisioningState" -o tsv 2>/dev/null)
[ "$guard_state" = Succeeded ] \
    && row "C precheck succeeds" PASS "$guard_state" \
    || row "C precheck succeeds" FAIL "expected Succeeded, got '${guard_state:-<unreadable>}' - reference() may name a field or api-version that does not exist"
ws_id=$(az deployment group show -g "$RG_C" -n precheck-workspace-exists \
    --query "properties.outputs.workspaceId.value" -o tsv 2>/dev/null)
printf '%s' "${ws_id:-}" | grep -Eq '^[0-9a-f]{8}-' \
    && row "C precheck resolves customerId" PASS "looks like a guid" \
    || row "C precheck resolves customerId" FAIL "output was '${ws_id:-<empty>}'"
[ "$state" = Succeeded ] \
    && row "C deployment succeeds" PASS "$state" \
    || row "C deployment succeeds" FAIL "expected Succeeded, got '${state:-<unreadable>}', failed step(s): ${steps:-<none>}"

# --- D: redeploy. The forceUpdateTag test, and it only shows in the count ---------------
echo "[5/5] Path D: redeploying $RG_APP ..."
deploy "$RG_APP" path-d WorkspaceName="$WS" DeployNewWorkspace=false
state=$(az deployment group show -g "$RG_APP" -n path-d --query properties.provisioningState -o tsv 2>/dev/null)
steps=$(failed_step "$RG_APP" path-d)
[ "$state" = Succeeded ] \
    && row "D redeploy succeeds" PASS "$state" \
    || row "D redeploy succeeds" FAIL "expected Succeeded, got '${state:-<unreadable>}', failed step(s): ${steps:-<none>}"
n=$(function_count "$RG_APP")
[ "${n:-0}" -ge 1 ] 2>/dev/null \
    && row "D still has the package after redeploy" PASS "$n function(s)" \
    || row "D still has the package after redeploy" FAIL "function count was '${n:-<unreadable>}' - the site PUT reset WEBSITE_RUN_FROM_PACKAGE and the push did not re-run"
s=$(pointer_shape "$RG_APP")
[ "$s" = blob ] \
    && row "D still stores the package where the app reloads it" PASS "pointer is a function-releases blob" \
    || row "D still stores the package where the app reloads it" FAIL "pointer shape is '$s' after the redeploy"

echo
if [ "$fails" -eq 0 ]; then
    echo "All four deployment paths behaved as asserted."
else
    echo "$fails assertion(s) failed."
fi
exit $((fails == 0 ? 0 : 1))
