# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Fmaster%2Fazuredeploy.json)

## Deployment

### Step 1: Deploy Infrastructure

Click the **Deploy to Azure** button above. Fill in the parameters and click **Create**.

### Step 2: Deploy Function Code

After the ARM template completes, deploy the function code:

```bash
git clone https://github.com/orcunsami/SOCRadar-Azure-Feeds.git
cd SOCRadar-Azure-Feeds/FunctionApp
zip -r /tmp/deploy.zip . --exclude "__pycache__/*" "*.pyc"
az functionapp deployment source config-zip -g <RESOURCE_GROUP> -n <FUNCTION_APP_NAME> --src /tmp/deploy.zip --build-remote true
```

The function app name is shown in the deployment outputs.

## Prerequisites

- Microsoft Sentinel workspace
- SOCRadar Platform API Key

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WorkspaceName` | - | Sentinel workspace name |
| `WorkspaceLocation` | RG location | Region of the workspace |
| `SocradarApiKey` | - | SOCRadar Platform API key |
| `IncludeAPTBlockHash` | true | Include APT Recommended Block Hash feed (~500 indicators) |
| `CustomCollectionIds` | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | "" | Comma-separated custom collection names |
| `PollingIntervalMinutes` | 60 | Polling interval (5-1440 minutes) |
| `EnableFeedsTable` | true | Store indicators in SOCRadar_Feeds_CL |
| `EnableAuditLogging` | true | Log operations to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | true | Deploy analytics dashboard |

## What Gets Deployed

- Azure Function App (Python 3.11, Consumption plan)
- Storage Account with checkpoint table for deduplication
- Sentinel TI indicators via batch upload API
- SOCRadar_Feeds_CL custom table (optional)
- SOCRadar_Feeds_Audit_CL audit table (optional)
- SOCRadar Threat Feeds Dashboard workbook (optional)

## Indicator Types

| Type | Pattern | Auto-detected From |
|------|---------|-------------------|
| IP | `[ipv4-addr:value = '1.2.3.4']` | ip type feeds |
| Domain | `[domain-name:value = 'evil.com']` | domain type feeds |
| URL | `[url:value = 'http://...']` | url type feeds |
| Hash (MD5/SHA-1/SHA-256) | `[file:hashes.MD5 = '...']` | 32/40/64-char hash |
| Email | `[email-addr:value = '...']` | email type feeds |

## Post-Deployment

The function runs automatically on the configured polling interval. First run imports all indicators, subsequent runs only import new ones (checkpoint-based deduplication).

## Support

- **Documentation:** [docs.socradar.io](https://docs.socradar.io)
- **Support:** support@socradar.io
