# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

## Deployment

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Ffunction%2Fazuredeploy.json)

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

- Microsoft Sentinel workspace
- SOCRadar Platform API Key

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `WorkspaceName` | Yes | - | Microsoft Sentinel workspace name |
| `WorkspaceLocation` | No | RG location | Region of the workspace |
| `SocradarApiKey` | Yes | - | SOCRadar Platform API key |
| `IncludeAPTBlockHash` | No | true | Include APT Recommended Block Hash feed (~500 indicators) |
| `CustomCollectionIds` | No | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | No | "" | Comma-separated custom collection names |
| `PollingIntervalMinutes` | No | 60 | Polling interval (5-1440 minutes) |
| `EnableFeedsTable` | No | true | Store indicators in SOCRadar_Feeds_CL |
| `EnableAuditLogging` | No | true | Log operations to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | No | true | Deploy analytics dashboard |

## What Gets Deployed

- **Azure Function App** (Python 3.11, Consumption plan) - Polls SOCRadar feeds on schedule
- **User-Assigned Managed Identity** - Secure access to Sentinel and Storage
- **Storage Account** - Checkpoint table for deduplication
- **DCE + DCR + Custom Tables** (optional) - SOCRadar_Feeds_CL and audit logging
- **Workbook** (optional) - SOCRadar Threat Feeds Dashboard

## Key Features

- STIX 2.1 indicator building (IP, domain, URL, file hash, email)
- Batch upload to Sentinel TI (100 indicators/batch)
- Checkpoint-based deduplication (only new indicators on each run)
- Custom collection support via SOCRadar API
- Managed Identity authentication (no stored credentials for Azure)

## Indicator Types

| Type | Pattern | Auto-detected From |
|------|---------|-------------------|
| IP | `[ipv4-addr:value = '1.2.3.4']` | ip type feeds |
| Domain | `[domain-name:value = 'evil.com']` | domain type feeds |
| URL | `[url:value = 'http://...']` | url type feeds |
| Hash (MD5/SHA-1/SHA-256) | `[file:hashes.MD5 = '...']` | 32/40/64-char hash |
| Email | `[email-addr:value = '...']` | email type feeds |

## Post-Deployment

Function polls on the configured schedule. First run imports all available indicators. Subsequent runs only import new ones (checkpoint-based deduplication).

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform.

Learn more at [socradar.io](https://socradar.io)

## Support

- **Documentation:** [docs.socradar.io](https://docs.socradar.io)
- **Support:** support@socradar.io
