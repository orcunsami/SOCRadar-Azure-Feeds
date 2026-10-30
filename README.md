# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

## Deployment

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Fmaster%2Fazuredeploy.json)

Click the **Deploy to Azure** button above. Fill in the parameters and click **Create**. The function app and code are deployed automatically.

Or via CLI:

```bash
az deployment group create \
  --resource-group <YOUR_RG> \
  --template-file azuredeploy.json \
  --parameters \
    WorkspaceName=<YOUR_WORKSPACE> \
    SocradarApiKey=<API_KEY>
```

`DeployNewWorkspace` defaults to `false`: the deployment uses your existing workspace and does not update the workspace itself, so its pricing tier (commitment tier included), retention and daily cap stay as they are. It does add its own tables under the workspace and enables Microsoft Sentinel on it if that is not done yet. If you do not have a workspace yet, add `DeployNewWorkspace=true` and the deployment creates one. Deploy in incremental mode (the default); complete mode deletes resources in the group that the template does not list.

## Prerequisites

- A Log Analytics workspace named `WorkspaceName` in the resource group you deploy to. By default (`DeployNewWorkspace=false`) it must already exist. To have it created, set `DeployNewWorkspace=true`.
- SOCRadar Platform API Key

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `WorkspaceName` | Yes | - | Microsoft Sentinel workspace name |
| `DeployNewWorkspace` | No | `false` | `false` uses the workspace that already exists: the workspace itself is not updated, so its pricing tier, retention and daily cap are never changed, and a name that does not exist fails before anything is created. Set `true` only to create a new workspace named `WorkspaceName`; do not set it on an existing workspace that uses a commitment tier. |
| `WorkspaceLocation` | No | RG location | Region of the workspace |
| `SocradarApiKey` | Yes | - | SOCRadar Platform API key |
| `CustomCollectionIds` | No | APT Block Hash | Comma-separated collection UUIDs. The default is the APT Recommended Block Hash feed (~500 indicators). Put other IDs here to enable the other recommended collections (see below). At least one collection must be set, or every run is recorded as `Failed`. |
| `CustomCollectionNames` | No | SOCRadar-APT-Recommended-Block-Hash | Comma-separated names, same order as the IDs. Used as the indicator label. A blank name is filled in for the seven recommended IDs; any other ID without a name becomes `Custom-Feed-<position>`, so name your own collections and keep the order. |
| `InitialLookbackDays` | No | 30 | First run of a collection sends only indicators last seen within this many days. `0` sends the whole feed. |
| `PackageUri` | No | release `v1.0.0` | URL of the Function App package. Leave the default unless you host the package yourself. |
| `PollingIntervalMinutes` | No | 60 | Polling interval (30-1440 minutes). 60 and above is rounded down to whole hours. The schedule is a cron expression, so pick a value that divides 60 (minutes) or 24 (hours): 45 minutes runs at :00 and :45, and 5 hours runs at 0, 5, 10, 15, 20 and then again after 4 hours. |
| `EnableFeedsTable` | No | true | Store indicators in SOCRadar_Feeds_CL |
| `EnableAuditLogging` | No | true | Log runs to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | No | true | Deploy the dashboard (needs `EnableFeedsTable`) |

## Recommended collections

The APT Block Hash feed is the default value of `CustomCollectionIds`. To enable another one, add its ID to that list.

| Collection | ID |
|------------|----|
| SOCRadar-APT-Recommended-Block-IP | `4d7a69ce6e7c49ff8c916da5d7343916` |
| SOCRadar-APT-Recommended-Block-Hash | `0cb06558728b4dc296019c93b78360d1` |
| SOCRadar-APT-Recommended-Block-Domain | `9079dcc2f96e4835bb807026d4cdcc86` |
| SOCRadar-Recommended-Block-Hash | `8742cab86cc4414092217f87298e94a1` |
| SOCRadar-Attackers-Recommended-Block-IP | `e89ab3b58e174b8c82767088d8e66cae` |
| SOCRadar-Attackers-Recommended-Block-Domain | `606a83358bbe466d8c3885e37fa595b7` |
| SOCRadar-Recommended-Phishing-Global | `03cc11380b5d4a77a0d0cc2a7c568230` |

## Existing installations

Deployments made before `DeployNewWorkspace` existed stated a pricing tier on the workspace
resource, and a template overwrites every field it states. If the target workspace was on a
**commitment tier**, redeploying reset it to `PerGB2018` (pay-as-you-go).

Check the current tier:

```bash
az monitor log-analytics workspace show -g <resource-group> -n <workspace> \
  --query "{sku:sku.name, lastSkuUpdate:sku.lastSkuUpdate}" -o json
```

If `lastSkuUpdate` lines up with when you deployed this integration and the tier isn't the one
you picked, reset your commitment tier from **Log Analytics workspaces > Usage and estimated
costs > Pricing tier**. The current template does not update an existing workspace while
`DeployNewWorkspace` is at its default `false`, so redeploying cannot change its pricing
tier, retention or daily cap.

Redeploy the template over an existing installation to pick up the `IndicatorsFailed` and `CollectionsFailed` audit columns; until then the data collection rule drops those two columns silently.

## How the code gets there

The template creates the Function App empty (`WEBSITE_RUN_FROM_PACKAGE=1`) and a deployment
script downloads `PackageUri`, verifies it is a readable zip, uploads it to the storage account
this template creates, and points the app at that blob with a read-only token. Azure stopped
accepting the creation of a Linux consumption Function App whose `WEBSITE_RUN_FROM_PACKAGE` is
a URL that redirects, and a GitHub release download URL always redirects.

One consequence worth knowing: **an installation keeps the package it was installed with.** The
release URL is read once, at install time. A later release does not reach an existing
installation -- redeploy to pick it up. Before September 2026 the app read that URL on every
cold start, so a new release did arrive on its own.

If the deployment fails at `triggerFirstRun`, the message says which step: an unreachable
package URL, a download that is not a readable zip, a package that never reached the container
the app reloads from, or an app that indexed no function within the poll window. The script
retries the settings read until its role assignment is effective, retries the upload up to
six times, and restarts the app once the package is staged -- writing the pointer alone was
measured not to make the host reload it. If the deployment fails, the diagnostic container
and its storage account stay in the resource group for 26 hours -- the documented ceiling --
so the log can still be read the next morning; delete them once you are done. A successful
deployment leaves neither behind.

It reports success only when both readings agree: the package pointer names a blob in the
`function-releases` container **and** the app has indexed a function. The count alone is not
enough -- an app whose package was staged somewhere else keeps reporting a function to Azure
Resource Manager while its host answers 503 from the next restart onwards.

A redeploy rewrites the package pointer, so it has to push again. If every attempt fails there,
the deployment reports a failure **and the app is left with no code** -- it does not keep
serving the package it had. Recovery does not need a rebuild: the previous package is still in
the `function-releases` container of the app's storage account, and pointing
`WEBSITE_RUN_FROM_PACKAGE` back at that blob brings the app back while you retry.
Rotating the storage account keys invalidates the read token inside that pointer, so the app
loses its code at the next restart -- issue a new token for the same blob and write the
pointer back.

One red row in **Resource group -> Deployments** is not ours and is harmless:
`Failure-Anomalies-Alert-Rule-Deployment-*`. Azure creates it by itself when the
Application Insights component appears, and it fails on a subscription that has
not registered the `Microsoft.AlertsManagement` provider (measured 7 Sep 2026).
Nothing in this template refers to it and the integration works without it;
register that provider if you want the smart-detection alert.

## What Gets Deployed

- **Log Analytics workspace** (when `DeployNewWorkspace` is `true` and it does not exist) with Microsoft Sentinel onboarded
- **Azure Function App** (Python 3.11, Consumption plan) - Polls SOCRadar feeds on schedule
- **Application Insights** - Step-by-step logging (workspace-based, 30 day retention)
- **User-Assigned Managed Identity** - no stored Azure credentials. Roles: Microsoft Sentinel Contributor on the workspace, Storage Table Data Contributor on the storage account, Monitoring Metrics Publisher on each DCR, Website Contributor on the Function App
- **Storage Account** - `FeedState` table holding one checkpoint per collection, and the `function-releases` container holding the package
- **DCE + DCR + Custom Tables** (optional) - SOCRadar_Feeds_CL and SOCRadar_Feeds_Audit_CL
- **Workbook** (optional) - SOCRadar Threat Feeds Dashboard
- **Deployment Script** - Downloads the package, stages it in the storage account, restarts the app and waits until a function is indexed (see above)

## How a run works

1. Each configured collection is fetched from the SOCRadar feed API.
2. Indicators last seen after the collection's checkpoint (minus a 48 hour overlap) are sent to Microsoft Sentinel TI in batches of 100. On the first run the window is `InitialLookbackDays`.
3. The checkpoint moves to the newest `latest_seen_date` that was delivered. If a batch never reaches Microsoft Sentinel the checkpoint stays where it was and the run is recorded as `PartialSuccess`; the next run sends those indicators again.
4. Indicator ids are stable (derived from type, value and collection), so re-sending an indicator updates the same indicator instead of creating a second one. `ThreatIntelIndicators` is append-only though: every send adds a row with the same `Id` and a newer `Modified`. A fresh deployment starts the app twice (the package is staged, then the app restarts) and every start runs an import; without the guard in point 9 that gave every indicator two or three rows within minutes (measured: 202 indicators, 424 rows). Count indicators, not rows: `ThreatIntelIndicators | where SourceSystem == "SOCRadar Threat Feeds" | summarize arg_max(TimeGenerated, *) by Id`. `IndicatorsCreated` in the audit table counts indicators sent, which includes those updates.
5. `SOCRadar_Feeds_CL` gets a row only for indicators last seen after the checkpoint (every indicator on a collection's first run), so the 48 hour overlap does not repeat rows there. The flip side: an indicator that reaches the feed late, with a `latest_seen_date` older than the checkpoint, is still sent to Microsoft Sentinel TI but gets no row in `SOCRadar_Feeds_CL`. Sentinel TI is the complete list; the table and dashboard count indicators newer than the checkpoint.
6. If the write to `SOCRadar_Feeds_CL` fails (for example a 403 right after deploy, while the DCR role assignment is still propagating), the checkpoint is held, the run is recorded as `PartialSuccess`, and the next run sends the indicators to Microsoft Sentinel TI again (an update) and writes the missing rows. The cost: until the table works, every run re-sends the whole window to Microsoft Sentinel TI. Rows that did land in a partly failed write can appear twice; the dashboard counts `dcount(IndicatorValue)`.
7. The same happens after an upload failure. If batch 1 reaches Microsoft Sentinel and batch 2 fails, `SOCRadar_Feeds_CL` already has the rows of batch 1, the checkpoint is held, and the recovery run selects those indicators as new again and writes their rows a second time (measured locally: 100 rows repeated for 150 indicators with 2 batches). Nothing is lost and the repeat is limited to the batches that landed before the failure; Microsoft Sentinel TI is unaffected (same ids). To count exactly, use KQL: `SOCRadar_Feeds_CL | distinct IndicatorValue | count`.
8. The dashboard tiles use `dcount`, which is approximate. On a live run of 3882 indicators it showed 3878 for the total (about 0.1% off) and 1903 / 1896 for two collections whose exact counts were 1888 / 1915 (about 1% off), so the tiles can differ slightly from the Microsoft Sentinel TI count. The exact count is `SOCRadar_Feeds_CL | distinct IndicatorValue | count`, or per collection `SOCRadar_Feeds_CL | distinct CollectionName, IndicatorValue | summarize count() by CollectionName`. The 4 missing in the total are not explained: an exact distinct count was not measured, and a value that appears in two collections would also lower it.
9. A collection that finished cleanly less than 15 minutes ago is not fetched again. Every host start runs an import, and a new deployment starts the host three times: when the package is staged, on the restart that follows, and about 5 to 8 minutes later when Azure creates its own smart-detection rule (measured 30 Oct 2026). The skipped runs are recorded as `Success` with 0 indicators. A run that lost indicators never counts as recent. This is why the polling interval starts at 30 minutes: a shorter one would have its scheduled runs skipped.

Indicators with an unsupported type or hash length are counted and skipped, not sent as a guessed type.

## Indicator Types

| Feed type | Pattern |
|------|---------|
| ip | `[ipv4-addr:value = '...']` |
| ipv6 | `[ipv6-addr:value = '...']` |
| domain, hostname | `[domain-name:value = '...']` |
| url | `[url:value = '...']` |
| hash (32 / 40 / 64 chars) | `[file:hashes.MD5 = '...']`, `SHA-1`, `SHA-256` |
| email | `[email-addr:value = '...']` |

## Post-Deployment

The deployment script verifies that the package URL answers and that the function was indexed, then restarts the app so the first import runs. Custom tables receive their first rows 10-15 minutes after the first run; that delay is normal for a newly created table.

### Audit table

`SOCRadar_Feeds_Audit_CL` gets one row per run:

| Status | Meaning |
|--------|---------|
| `Success` | every collection was delivered completely |
| `PartialSuccess` | at least one collection failed or lost indicators; `ErrorMessage` says which. Lost indicators are sent again on the next run |
| `Failed` | the run itself failed before any collection completed, or no collection is configured |

### Managing Collections

1. Go to your **Function App** in Azure Portal
2. Open **Settings > Environment variables**
3. Edit:
   - `CUSTOM_COLLECTION_IDS` — comma-separated collection UUIDs (the APT Block Hash feed is in here by default)
   - `CUSTOM_COLLECTION_NAMES` — comma-separated names (same order as IDs)
4. Click **Apply** — the Function App restarts and, because the timer runs on startup, imports immediately

New collections start from `InitialLookbackDays`. Removed collections leave harmless orphan checkpoints in Table Storage.

### Monitoring Logs

1. Go to your **Function App** in Azure Portal
2. **Monitoring > Log stream** for real-time logs
3. Or **Application Insights > Logs**:

```kql
traces
| where timestamp > ago(1h)
| where message has "Step"
| order by timestamp desc
```

## Development

```bash
python3 tests/run_all.py        # unit tests, no Azure needed
python3 tests/mutate.py         # proves the tests catch the bugs they exist for
TEST_SUBSCRIPTION_ID=<id> bash scripts/test_deploy_paths.sh   # live deploy paths; <id> must be the active az subscription
bash scripts/portal_test.sh     # live: two imports, judged from SOCRadar_Feeds_Audit_CL and ThreatIntelIndicators (needs scripts/test.config)
python3 scripts/build_package.py --out dist/FunctionApp.zip --deps-from <released FunctionApp.zip>
```

Build the package with the script, not `zip -r`: archives from the macOS zip tool can carry entries the Linux worker cannot read, and the host then indexes zero functions without reporting an error.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform. Learn more at [socradar.io](https://socradar.io)

## Support

integration@socradar.io
