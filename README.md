# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Fmaster%2Fazuredeploy.json)

## Prerequisites

- Microsoft Sentinel workspace
- SOCRadar Platform API Key

## Configuration

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `WorkspaceName` | Your Sentinel workspace name (e.g., `my-sentinel-workspace`, NOT the Workspace ID/GUID) |
| `WorkspaceLocation` | Region of your workspace (e.g., `centralus`, `northeurope`) |
| `SocradarApiKey` | Your SOCRadar Platform API key |

### Recommended Feed Collections

Each collection can be individually enabled/disabled during deployment (all enabled by default):

| Parameter | Default | Feed Collection |
|-----------|---------|-----------------|
| `IncludeAPTBlockHash` | **true** | SOCRadar APT Recommended Block Hash (~600 indicators) |
| `IncludeAPTBlockIP` | false | SOCRadar APT Recommended Block IP (~2000 indicators) |
| `IncludeAPTBlockDomain` | false | SOCRadar APT Recommended Block Domain (~5900 indicators) |
| `IncludeBlockHash` | false | SOCRadar Recommended Block Hash (~1400 indicators) |
| `IncludeAttackersBlockIP` | false | SOCRadar Attackers Recommended Block IP (~3750 indicators) |
| `IncludeAttackersBlockDomain` | false | SOCRadar Attackers Recommended Block Domain (~2850 indicators) |
| `IncludePhishingGlobal` | false | SOCRadar Recommended Phishing Global (~750 indicators) |

### Other Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CustomCollectionIds` | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | "" | Comma-separated custom collection names (matching IDs order) |
| `PollingIntervalMinutes` | 60 | How often to poll feeds (5-1440 minutes) |
| `EnableFeedsTable` | true | Store indicators in SOCRadar_Feeds_CL custom table |
| `EnableAuditLogging` | true | Log operations to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | true | Deploy SOCRadar Threat Feeds Analytics Dashboard |

## What Gets Deployed

- **SOCRadar-Feeds-Import** - Logic App that polls SOCRadar feeds and imports indicators as TI
- **Storage Account** - Checkpoint state for deduplication
- **Sentinel TI Indicators** - Imported as TiIndicators in your workspace
- **SOCRadar_Feeds_CL** - Custom table for indicator analytics (if EnableFeedsTable=true)
- **SOCRadar_Feeds_Audit_CL** - Audit log table (if EnableAuditLogging=true)
- **SOCRadar Threat Feeds Dashboard** - Workbook with indicator charts and audit monitoring (if EnableWorkbook=true)

## Key Features

- 7 recommended threat feed collections (individually selectable)
- Custom feed collections (indicator type auto-detected from feed data)
- STIX 2.1 pattern generation for Sentinel TI ingestion
- Hash type auto-detection: MD5 (32), SHA-1 (40), SHA-256 (64 chars)
- Dynamic threat type classification per collection (Phishing, Malicious-Activity, Malware)
- ValidUntil scheduling (90 days from last seen date)
- Checkpoint-based deduplication to prevent duplicate imports
- Optional audit logging to Log Analytics

## Indicator Types Supported

| STIX Type | Pattern Example | Auto-detected From |
|-----------|-----------------|-------------------|
| IP | `[ipv4-addr:value = '1.2.3.4']` | ip type feeds |
| Domain | `[domain-name:value = 'evil.com']` | domain type feeds |
| URL | `[url:value = 'http://...']` | url type feeds |
| Hash (MD5) | `[file:hashes.MD5 = '...']` | 32-char hash |
| Hash (SHA-1) | `[file:hashes.'SHA-1' = '...']` | 40-char hash |
| Hash (SHA-256) | `[file:hashes.'SHA-256' = '...']` | 64-char hash |
| Email | `[email-addr:value = '...']` | email type feeds |

## Post-Deployment

Logic Apps are configured to start **3 minutes after deployment** to allow Azure role propagation.

No manual action required - they will start automatically.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform.

Learn more at [socradar.io](https://socradar.io)

## Support

- **Documentation:** [docs.socradar.io](https://docs.socradar.io)
- **Support:** support@socradar.io
