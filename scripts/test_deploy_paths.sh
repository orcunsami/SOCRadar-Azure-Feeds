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
#                   at 15:10 on 7 Sep 2026. The package is now staged as a blob
#                   by the deploymentScript.
#
# Four paths:
#   A  missing workspace, defaults (DeployNewWorkspace=false) -> fails, resource group stays EMPTY
#   B  DeployNewWorkspace=true (opt-in create) -> succeeds, guard SKIPPED, functions INDEXED
#   C  existing workspace, defaults       -> guard SUCCEEDS and resolves customerId
#   D  redeploy of B at the defaults      -> succeeds, workspace untouched, the app still has functions
#   E  existing workspace, a key SOCRadar rejects -> Failed in triggerFirstRun, the banner text says so
#   F  what B's first window left in the workspace -> one import, the later host start skipped
#      (Success with 0 created), every indicator tagged with the collection name
#
# The deployment checks the SOCRadar API key (a 401 fails it), so B, C and D need a key
# SOCRadar accepts: TEST_SOCRADAR_API_KEY is required. "placeholder" no longer deploys.
# F waits up to 25 minutes for the workspace (ingestion lags); TEST_FIRST_RUN_WAIT and
# TEST_POLL (seconds) shorten that for the fake-az tests.
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
#
# Usage (deploys into live Azure; the subscription must be the active az one):
#   TEST_SUBSCRIPTION_ID=<id> bash scripts/test_deploy_paths.sh
#   optional: TEST_LOCATION, TEST_SOCRADAR_API_KEY, KEEP_RESOURCES
# `bash -x` would print the API key assignments; no xtrace in this script.
{ set +x; } 2>/dev/null
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$REPO_ROOT/azuredeploy.json"

LOCATION="${TEST_LOCATION:-northeurope}"
API_KEY="${TEST_SOCRADAR_API_KEY:-}"
KEEP="${KEEP_RESOURCES:-false}"

SFX=$(python3 -c "import uuid;print(uuid.uuid4().hex[:4])") && [ -n "$SFX" ] || { echo "ERROR: CANNOT MEASURE: no suffix for the resource group names"; exit 1; }
RG_APP="rg-feeds-paths-$SFX"
RG_C="rg-feeds-paths-c-$SFX"
RG_E="rg-feeds-paths-e-$SFX"
DEAD_KEY="invalid-key-$SFX"
WS="ws-feeds-paths-$SFX"
MISSING="ws-does-not-exist-$SFX"

# Every `az` call below runs against whatever subscription is active. Say which
# one is meant and refuse to continue on any other.
if ! ACTIVE_SUB=$(az account show --query id -o tsv); then
    echo "ERROR: CANNOT MEASURE: az account show failed (reason above)"
    exit 1
fi
if [ -z "${TEST_SUBSCRIPTION_ID:-}" ] || [ "$ACTIVE_SUB" != "$TEST_SUBSCRIPTION_ID" ]; then
    echo "ERROR: set TEST_SUBSCRIPTION_ID to the subscription to deploy into; az is on '${ACTIVE_SUB:-none}'"
    exit 1
fi

if [ -z "$API_KEY" ]; then
    echo "ERROR: set TEST_SOCRADAR_API_KEY to a key SOCRadar accepts: the deployment fails on a 401 and B, C and D would fail for that reason"
    exit 1
fi

fails=0
UNREAD="<unreadable>"
row() {  # row <name> <PASS|FAIL|CANNOT> <detail>; CANNOT = the look failed, which is not a FAIL and not a PASS
    printf '%-40s %-6s %s\n' "$1" "$2" "$3"
    [ "$2" = PASS ] || fails=$((fails + 1))
}

# rd <var> <command...>: stdout into var; a failed command puts UNREAD there and shows its reason.
rd() {
    local var="$1" out rc err; shift
    err=$(mktemp)
    out=$("$@" 2>"$err"); rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "  CANNOT MEASURE: ${*:1:4} failed (rc=$rc): $(head -c 200 "$err" | tr '\n' ' ')"
        out="$UNREAD"
    fi
    rm -f "$err"
    printf -v "$var" '%s' "$out"
}

# expect <name> <got> <want> <pass detail> <fail detail>
expect() {
    if [ "$2" = "$UNREAD" ]; then row "$1" CANNOT "the az read failed (reason above)"
    elif [ "$2" = "$3" ]; then row "$1" PASS "$4"
    else row "$1" FAIL "$5"; fi
}

cleanup() {
    local rc=$? bad=0
    rm -f "$SECRETS_FILE" "$DEAD_FILE"
    if [ "$KEEP" = true ]; then
        echo "KEEP_RESOURCES=true, leaving $RG_APP, $RG_C and $RG_E in place"
        return
    fi
    local queued="" exists
    for rg in "$RG_APP" "$RG_C" "$RG_E"; do
        # A group the early exit never created has nothing to delete (and nothing to bill).
        # A failed look is not "absent": try the delete anyway.
        exists=$(az group exists -n "$rg" 2>/dev/null) || exists=unknown
        [ "$exists" = false ] && continue
        if ! az group delete -n "$rg" --yes --no-wait -o none; then
            echo "ERROR: delete of $rg was NOT queued: it may still be billing, delete it by hand"; bad=1
        else
            queued="$queued $rg"
        fi
    done
    [ "$bad" -eq 0 ] && [ -n "$queued" ] && echo "cleanup: delete queued for$queued"
    [ "$rc" -eq 0 ] && [ "$bad" -ne 0 ] && exit 1
    return 0
}
SECRETS_FILE=""; DEAD_FILE=""
trap cleanup EXIT

# The key goes in a 0600 file, not on the az command line (ps shows argv).
umask 077
SECRETS_FILE=$(mktemp)
DEAD_FILE=$(mktemp)
mkparams() {  # mkparams <key> <file>
    SK="$1" python3 -c '
import json, os
print(json.dumps({"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                  "contentVersion": "1.0.0.0", "parameters": {"SocradarApiKey": {"value": os.environ["SK"]}}}))' > "$2"
}
mkparams "$API_KEY" "$SECRETS_FILE"
mkparams "$DEAD_KEY" "$DEAD_FILE"

deploy() {  # deploy <rg> <name> <extra params...>
    local rg="$1" name="$2"; shift 2
    local err rc msg
    err=$(mktemp)
    az deployment group create -g "$rg" -n "$name" --template-file "$TEMPLATE" \
        --parameters @"${PARAM_FILE:-$SECRETS_FILE}" WorkspaceLocation="$LOCATION" "$@" \
        -o none 2>"$err"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        msg=$(head -c 800 "$err")
        echo "  deployment $name failed: ${msg//"$API_KEY"/***}"
    fi
    rm -f "$err"
    return "$rc"
}

# The only signal that separates a loaded package from a Running empty app.
# Read through ARM rather than `az functionapp function list`: that command
# returned an empty string right after a deployment while the ARM call already
# reported 1, and an empty read is indistinguishable from a real zero.
function_count() {  # function_count <rg>: a number, no-app (listed, none there) or unreadable
    local rg="$1" app n read_ok=0
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv) || { echo unreadable; return; }
    [ -n "$app" ] || { echo "no-app"; return; }
    for _ in $(seq 1 6); do
        if n=$(az rest --method get --url \
            "https://management.azure.com/subscriptions/$ACTIVE_SUB/resourceGroups/$rg/providers/Microsoft.Web/sites/$app/functions?api-version=2023-12-01" \
            --query "length(value)" -o tsv 2>/dev/null); then
            read_ok=1
            case "$n" in ''|*[!0-9]*) ;; *) [ "$n" -ge 1 ] && { echo "$n"; return; };; esac
        fi
        python3 -c "import time;time.sleep(15)"
    done
    [ "$read_ok" -eq 1 ] && echo "${n:-0}" || echo unreadable
}

# The count is ARM metadata and it lies on its own: on a rig app whose pointer
# was left at "1", ARM kept reporting 1 function while the host answered 503 for
# three and a half minutes. The pointer's SHAPE is the second reading -- the
# package has to sit in the function-releases container the app reloads from.
# The value itself is never printed: it carries a SAS token.
pointer_shape() {  # pointer_shape <rg>
    local rg="$1" app v
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv) || { echo unreadable; return; }
    [ -n "$app" ] || { echo "no-app"; return; }
    # An empty read is "could not read", not "missing": on 10 Sep 2026 one read
    # came back empty on an app whose pointer was a function-releases blob, and
    # the path was reported red. Read up to three times, then say which it was.
    local rc=1 i
    for i in 1 2 3; do
        v=$(az functionapp config appsettings list -g "$rg" -n "$app" \
            --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value" -o tsv 2>/dev/null) && rc=0 && break
        sleep 10
    done
    case "$v" in
        *function-releases*) echo blob ;;
        1) echo one ;;
        "") [ "$rc" = 0 ] && echo missing || echo unreadable ;;
        *) echo other ;;
    esac
}

# A package the host cannot reload survives until the first restart, so
# restart it here.
survives_restart() {  # survives_restart <rg>
    local rg="$1" app code
    app=$(az functionapp list -g "$rg" --query "[0].name" -o tsv) || { echo unreadable; return; }
    [ -n "$app" ] || { echo "no-app"; return; }
    # A restart that did not happen leaves the old process answering 200: not a pass.
    az functionapp restart -g "$rg" -n "$app" -o none 2>/dev/null || { echo restart-failed; return; }
    for _ in $(seq 1 12); do
        python3 -c "import time;time.sleep(15)"
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$app.azurewebsites.net/")
        [ "$code" = 200 ] && { echo 200; return; }
    done
    echo "${code:-no-answer}"
}

# Which step failed, read from the deployment. Never inferred from the state:
# a Function App failure once got reported as "the guard blocks a workspace".
failed_step() {  # failed_step <rg> <deployment>: names, empty, or UNREAD
    local out
    out=$(az deployment operation group list -g "$1" -n "$2" \
        --query "[?properties.provisioningState=='Failed'].properties.targetResource.resourceName" \
        -o tsv 2>/dev/null) || { echo "$UNREAD"; return; }
    printf '%s\n' "$out" | sort -u | tr '\n' ' '
}

echo "[1/7] Creating $RG_APP, $RG_C and $RG_E ..."
for rg in "$RG_APP" "$RG_C" "$RG_E"; do
    az group create -n "$rg" -l "$LOCATION" -o none || { echo "ERROR: CANNOT MEASURE: could not create $rg"; exit 1; }
done

# --- A: the customer's typo, defaults left alone --------------------------------------
echo "[2/7] Path A: missing workspace with DeployNewWorkspace=false ..."
deploy "$RG_APP" path-a WorkspaceName="$MISSING"
rd state az deployment group show -g "$RG_APP" -n path-a --query properties.provisioningState -o tsv
rd left az resource list -g "$RG_APP" --query "length(@)" -o tsv
steps=$(failed_step "$RG_APP" path-a)

expect "A deployment fails" "$state" Failed "$state" "expected Failed, got '$state'"
expect "A leaves nothing behind" "$left" 0 "0 resources" "expected 0, found '$left' - the guard did not hold"
if [ "$steps" = "$UNREAD" ]; then row "A blames the precheck" CANNOT "the az read failed"
elif printf '%s' "$steps" | grep -q 'precheck-workspace-exists'; then row "A blames the precheck" PASS "precheck-workspace-exists"
else row "A blames the precheck" FAIL "failed step(s) were: ${steps:-<none read>}"; fi

# --- B: greenfield, and the package has to actually load -------------------------------
echo "[3/7] Path B: new workspace name with the parameters at their defaults ..."
deploy "$RG_APP" path-b WorkspaceName="$WS" DeployNewWorkspace=true
rd state az deployment group show -g "$RG_APP" -n path-b --query properties.provisioningState -o tsv
steps=$(failed_step "$RG_APP" path-b)
expect "B greenfield succeeds" "$state" Succeeded "$state" "expected Succeeded, got '$state', failed step(s): ${steps:-<none>}"
rd guard_ran az deployment operation group list -g "$RG_APP" -n path-b \
    --query "[?properties.targetResource.resourceName=='precheck-workspace-exists'] | length(@)" -o tsv
expect "B skips the precheck" "$guard_ran" 0 "condition false" "the precheck ran against a workspace being created (count '$guard_ran')"
n=$(function_count "$RG_APP")
s=$(pointer_shape "$RG_APP")
case "$n" in
    unreadable) row "B indexes the function" CANNOT "the function list could not be read" ;;
    ''|*[!0-9]*) row "B indexes the function" FAIL "function count was '$n' with the pointer '$s'" ;;
    *) [ "$n" -ge 1 ] \
        && row "B indexes the function" PASS "$n function(s)" \
        || row "B indexes the function" FAIL "function count was '$n' with the pointer '$s' - a blob pointer here means the package arrived and only the indexing was slower than the script's window" ;;
esac
case "$s" in
    blob) row "B stores the package where the app reloads it" PASS "pointer is a function-releases blob" ;;
    unreadable) row "B stores the package where the app reloads it" CANNOT "the app setting could not be read" ;;
    *) row "B stores the package where the app reloads it" FAIL "pointer shape is '$s' - with 'one' the host answers 503 after a restart while ARM still reports a function" ;;
esac
s=$(survives_restart "$RG_APP")
case "$s" in
    200) row "B still serves after a restart" PASS "host answered 200" ;;
    unreadable|restart-failed) row "B still serves after a restart" CANNOT "$s: the restart did not happen" ;;
    *) row "B still serves after a restart" FAIL "host answered '$s' - the install works until the first restart" ;;
esac

# --- C: the guard's success branch, which B never exercises ----------------------------
echo "[4/7] Path C: existing workspace, DeployNewWorkspace=false ..."
if ! az monitor log-analytics workspace create -g "$RG_C" -n "$WS" -l "$LOCATION" --retention-time 30 -o none; then
    row "C workspace for the test" CANNOT "az monitor log-analytics workspace create failed (reason above)"
else
    deploy "$RG_C" path-c WorkspaceName="$WS"
    rd state az deployment group show -g "$RG_C" -n path-c --query properties.provisioningState -o tsv
    steps=$(failed_step "$RG_C" path-c)
    rd guard_state az deployment operation group list -g "$RG_C" -n path-c \
        --query "[?properties.targetResource.resourceName=='precheck-workspace-exists'].properties.provisioningState" -o tsv
    expect "C precheck succeeds" "$guard_state" Succeeded "$guard_state" "expected Succeeded, got '$guard_state' - reference() may name a field or api-version that does not exist"
    rd ws_id az deployment group show -g "$RG_C" -n precheck-workspace-exists --query "properties.outputs.workspaceId.value" -o tsv
    if [ "$ws_id" = "$UNREAD" ]; then row "C precheck resolves customerId" CANNOT "the az read failed (reason above)"
    elif printf '%s' "$ws_id" | grep -Eq '^[0-9a-f]{8}-'; then row "C precheck resolves customerId" PASS "looks like a guid"
    else row "C precheck resolves customerId" FAIL "output was '${ws_id:-<empty>}'"; fi
    expect "C deployment succeeds" "$state" Succeeded "$state" "expected Succeeded, got '$state', failed step(s): ${steps:-<none>}"
fi

# --- D: redeploy. The forceUpdateTag test, and it only shows in the count ---------------
echo "[5/7] Path D: redeploying $RG_APP at the defaults ..."
# Non-default retention first: the redeploy must leave it, and lastSkuUpdate, exactly as they were.
az monitor log-analytics workspace update -g "$RG_APP" -n "$WS" --retention-time 90 -o none 2>/dev/null
before=$(az monitor log-analytics workspace show -g "$RG_APP" -n "$WS" --query "[retentionInDays, sku.name, sku.lastSkuUpdate]" -o tsv 2>/dev/null | tr '\n' ' ')
deploy "$RG_APP" path-d WorkspaceName="$WS"
after=$(az monitor log-analytics workspace show -g "$RG_APP" -n "$WS" --query "[retentionInDays, sku.name, sku.lastSkuUpdate]" -o tsv 2>/dev/null | tr '\n' ' ')
[ -n "$before" ] && [ "$before" = "$after" ] && row "D leaves the existing workspace untouched" PASS "retention/sku/lastSkuUpdate unchanged" || row "D leaves the existing workspace untouched" FAIL "before='$before' after='$after'"
rd state az deployment group show -g "$RG_APP" -n path-d --query properties.provisioningState -o tsv
steps=$(failed_step "$RG_APP" path-d)
expect "D redeploy succeeds" "$state" Succeeded "$state" "expected Succeeded, got '$state', failed step(s): ${steps:-<none>}"
n=$(function_count "$RG_APP")
case "$n" in
    unreadable) row "D still has the package after redeploy" CANNOT "the function list could not be read" ;;
    ''|*[!0-9]*) row "D still has the package after redeploy" FAIL "function count was '$n'" ;;
    *) [ "$n" -ge 1 ] \
        && row "D still has the package after redeploy" PASS "$n function(s)" \
        || row "D still has the package after redeploy" FAIL "function count was '$n' - the site PUT reset WEBSITE_RUN_FROM_PACKAGE and the push did not re-run" ;;
esac
s=$(pointer_shape "$RG_APP")
case "$s" in
    blob) row "D still stores the package where the app reloads it" PASS "pointer is a function-releases blob" ;;
    unreadable) row "D still stores the package where the app reloads it" CANNOT "the app setting could not be read" ;;
    *) row "D still stores the package where the app reloads it" FAIL "pointer shape is '$s' after the redeploy" ;;
esac

# --- E: the key SOCRadar rejects stops the deployment, and says why -------------------------
echo "[6/7] Path E: existing workspace, a key SOCRadar rejects ..."
if ! az monitor log-analytics workspace create -g "$RG_E" -n "$WS" -l "$LOCATION" --retention-time 30 -o none; then
    row "E workspace for the test" CANNOT "az monitor log-analytics workspace create failed (reason above)"
else
    PARAM_FILE="$DEAD_FILE" deploy "$RG_E" path-e WorkspaceName="$WS"
    rd state az deployment group show -g "$RG_E" -n path-e --query properties.provisioningState -o tsv
    steps=$(failed_step "$RG_E" path-e)
    expect "E deployment fails" "$state" Failed "$state" "expected Failed, got '$state': a rejected key must not give a green deploy"
    if [ "$steps" = "$UNREAD" ]; then row "E fails in the key check" CANNOT "the az read failed"
    elif printf '%s' "$steps" | grep -q 'triggerFirstRun'; then row "E fails in the key check" PASS "triggerFirstRun"
    else row "E fails in the key check" FAIL "failed step(s) were: ${steps:-<none read>}"; fi
    # The text the portal banner shows is the failed operation's status message.
    rd msg az deployment operation group list -g "$RG_E" -n path-e \
        --query "[?properties.targetResource.resourceName=='triggerFirstRun'].properties.statusMessage" -o json
    if [ "$msg" = "$UNREAD" ]; then row "E banner names the rejected key" CANNOT "the az read failed"
    elif printf '%s' "$msg" | grep -q 'rejected the API key'; then row "E banner names the rejected key" PASS "message carries 'rejected the API key'"
    else row "E banner names the rejected key" FAIL "status message was: $(printf '%s' "$msg" | head -c 200)"; fi
fi

# --- F: what the first window left in the workspace ------------------------------------
echo "[7/7] Path F: first-run audit and indicator names in the workspace of path B (up to 25 min) ..."
rd cust az monitor log-analytics workspace show -g "$RG_APP" -n "$WS" --query customerId -o tsv
if [ "$cust" = "$UNREAD" ]; then
    row "F first window readable" CANNOT "the workspace id could not be read"
else
    # Ingestion lags by minutes: poll until the first import's audit row, and a row
    # for another host start inside its 15 minute window, are both visible.
    res=$(python3 - "$cust" "Block-Hash" <<'PYEOF'
import json, os, subprocess, sys, time
from datetime import datetime, timedelta
cust = sys.argv[1]
POLL = int(os.environ.get("TEST_POLL", "30"))
def q(kql):
    r = subprocess.run(["az", "monitor", "log-analytics", "query", "-w", cust, "--analytics-query", kql, "-o", "json"],
                       capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None
def ts(v):
    v = v.rstrip("Z")
    if "." in v:
        a, b = v.split(".")
        v = a + "." + b[:6]
    return datetime.fromisoformat(v)
deadline = time.time() + int(os.environ.get("TEST_FIRST_RUN_WAIT", "1500"))
window = None
while time.time() < deadline:
    rows = q("SOCRadar_Feeds_Audit_CL | project TimeGenerated, Status, IndicatorsCreated | order by TimeGenerated asc")
    if rows is not None:
        made = [r for r in rows if int(r.get("IndicatorsCreated") or 0) > 0]
        if made:
            t0 = ts(made[0]["TimeGenerated"])
            inwin = [r for r in rows if t0 <= ts(r["TimeGenerated"]) <= t0 + timedelta(minutes=15)]
            window = (len(inwin), sum(1 for r in inwin if int(r.get("IndicatorsCreated") or 0) > 0),
                      sum(1 for r in rows if r["Status"] != "Success"))
            if len(inwin) >= 2:
                break
    time.sleep(POLL)
if window is None:
    print("UNREADABLE"); sys.exit(0)
ti = None
while time.time() < deadline + 300:
    ti = q("ThreatIntelIndicators | where SourceSystem == 'SOCRadar Threat Feeds' | summarize n=count(), tagged=countif(Tags has 'SOCRadar-APT-Recommended-Block-Hash')")
    if ti and int(ti[0]["n"]) > 0:
        break
    time.sleep(POLL)
n, tagged = (int(ti[0]["n"]), int(ti[0]["tagged"])) if ti else (-1, -1)
print("RESULT", window[0], window[1], window[2], n, tagged)
PYEOF
)
    read -r tag w_rows w_made bad ti_n ti_tagged <<< "$res"
    if [ "$tag" != RESULT ]; then
        row "F first window readable" CANNOT "the audit table never answered with an import row: $res"
    else
        expect "F first import ran once in its window" "$w_made" 1 "1 row with created > 0" "$w_made rows with created > 0 inside 15 minutes: the host-start guard did not hold"
        if [ "$w_rows" -ge 2 ]; then row "F later host start recorded as skipped" PASS "$((w_rows - 1)) more row(s) in the window, 0 created"
        else row "F later host start recorded as skipped" CANNOT "only one audit row in the window: no second host start was observed"; fi
        expect "F no audit row is a failure" "$bad" 0 "every row Success" "$bad audit row(s) not Success"
        if [ "$ti_n" -gt 0 ] && [ "$ti_n" = "$ti_tagged" ]; then row "F indicators carry the collection name" PASS "$ti_n of $ti_n tagged"
        else row "F indicators carry the collection name" FAIL "$ti_tagged of $ti_n indicators carry the name tag"; fi
    fi
fi

echo
if [ "$fails" -eq 0 ]; then
    echo "All six deployment paths behaved as asserted."
else
    echo "$fails assertion(s) failed."
fi
exit $((fails == 0 ? 0 : 1))
