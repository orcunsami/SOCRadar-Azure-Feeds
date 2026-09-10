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

## Prerequisites

- Microsoft Sentinel workspace **in the same resource group** you deploy to
- SOCRadar Platform API Key

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `WorkspaceName` | Yes | - | Microsoft Sentinel workspace name |
| `DeployNewWorkspace` | No | `true` | Create `WorkspaceName` when it does not exist yet; an existing workspace under that name is left untouched (pricing tier, retention, daily cap). Set `false` when the workspace must already exist, so a misspelled name fails before anything is created. An existing workspace in another region than `WorkspaceLocation` fails with `InvalidResourceLocation`. |
| `WorkspaceLocation` | No | RG location | Region of the workspace |
| `SocradarApiKey` | Yes | - | SOCRadar Platform API key |
| `IncludeAPTBlockHash` | No | true | Include APT Recommended Block Hash feed (~500 indicators) |
| `CustomCollectionIds` | No | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | No | "" | Comma-separated custom collection names |
| `InitialLookbackDays` | No | 30 | First run of a collection sends only indicators last seen within this many days. `0` sends the whole feed. |
| `PollingIntervalMinutes` | No | 60 | Polling interval (5-1440 minutes). 60 and above is rounded down to whole hours. |
| `EnableFeedsTable` | No | true | Store indicators in SOCRadar_Feeds_CL |
| `EnableAuditLogging` | No | true | Log runs to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | No | true | Deploy the dashboard (needs `EnableFeedsTable`) |

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
costs > Pricing tier**. The current template states no workspace-level settings at all, so
redeploying -- with `DeployNewWorkspace` at its default `true` -- cannot change its pricing
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

- **Azure Function App** (Python 3.11, Consumption plan) - Polls SOCRadar feeds on schedule
- **Application Insights** - Step-by-step logging (workspace-based, 30 day retention)
- **User-Assigned Managed Identity** - Access to Microsoft Sentinel and Storage, no stored Azure credentials
- **Storage Account** - `FeedState` table holding one checkpoint per collection
- **DCE + DCR + Custom Tables** (optional) - SOCRadar_Feeds_CL and SOCRadar_Feeds_Audit_CL
- **Workbook** (optional) - SOCRadar Threat Feeds Dashboard
- **Deployment Script** - Checks the package loaded and triggers the first import

## How a run works

1. Each configured collection is fetched from the SOCRadar feed API.
2. Indicators last seen after the collection's checkpoint (minus a 48 hour overlap) are sent to Microsoft Sentinel TI in batches of 100. On the first run the window is `InitialLookbackDays`.
3. The checkpoint moves to the newest `latest_seen_date` that was delivered. If a batch never reaches Microsoft Sentinel the checkpoint stays where it was and the run is recorded as `PartialSuccess`; the next run sends those indicators again.
4. Indicator ids are stable (derived from type, value and collection), so re-sending an indicator updates the existing record instead of creating a copy.

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
| `Failed` | the run itself failed before any collection completed |

### Managing Collections

1. Go to your **Function App** in Azure Portal
2. Open **Settings > Environment variables**
3. Edit:
   - `CUSTOM_COLLECTION_IDS` — comma-separated collection UUIDs
   - `CUSTOM_COLLECTION_NAMES` — comma-separated names (same order as IDs)
   - `INCLUDE_0cb06558728b4dc296019c93b78360d1` — `True` or `False` for the APT Block Hash feed
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
python3 scripts/build_package.py --out dist/FunctionApp.zip --deps-from <released FunctionApp.zip>
```

Build the package with the script, not `zip -r`: archives from the macOS zip tool can carry entries the Linux worker cannot read, and the host then indexes zero functions without reporting an error.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform. Learn more at [socradar.io](https://socradar.io)

## Support

integration@socradar.io
