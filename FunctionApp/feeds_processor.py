"""
SOCRadar Feeds Processor
Fetches feeds from SOCRadar API, filters by checkpoint, uploads to Sentinel TI in batches.
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Tuple

import requests
from azure.identity import DefaultAzureCredential
from azure.data.tables import TableServiceClient

from stix_builder import StixBuilder
from dcr_logger import DcrLogger

logger = logging.getLogger(__name__)

RECOMMENDED_COLLECTIONS = {
    "4d7a69ce6e7c49ff8c916da5d7343916": "SOCRadar-APT-Recommended-Block-IP",
    "0cb06558728b4dc296019c93b78360d1": "SOCRadar-APT-Recommended-Block-Hash",
    "9079dcc2f96e4835bb807026d4cdcc86": "SOCRadar-APT-Recommended-Block-Domain",
    "8742cab86cc4414092217f87298e94a1": "SOCRadar-Recommended-Block-Hash",
    "e89ab3b58e174b8c82767088d8e66cae": "SOCRadar-Attackers-Recommended-Block-IP",
    "606a83358bbe466d8c3885e37fa595b7": "SOCRadar-Attackers-Recommended-Block-Domain",
    "03cc11380b5d4a77a0d0cc2a7c568230": "SOCRadar-Recommended-Phishing-Global",
}

SOCRADAR_FEED_URL = "https://platform.socradar.com/api/threat/intelligence/feed_list"
SENTINEL_UPLOAD_URL = "https://sentinelus.azure-api.net/workspaces/{workspace_id}/threatintelligenceindicators:upload"
BATCH_SIZE = 100


class FeedsProcessor:

    def __init__(self, config: dict):
        self.api_key = config["socradar_api_key"]
        self.workspace_id = config["workspace_id"]
        self.subscription_id = config["subscription_id"]
        self.resource_group = config["resource_group"]
        self.workspace_name = config["workspace_name"]
        self.storage_account_name = config["storage_account_name"]
        self.collections = config["collections"]
        self.enable_feeds_table = config.get("enable_feeds_table", False)
        self.enable_audit_logging = config.get("enable_audit_logging", False)

        self.credential = DefaultAzureCredential()
        self._mgmt_token = None

        table_url = f"https://{self.storage_account_name}.table.core.windows.net"
        self.table_client = TableServiceClient(
            endpoint=table_url, credential=self.credential
        ).get_table_client("FeedState")

        self.dcr_logger = None
        if self.enable_feeds_table or self.enable_audit_logging:
            self.dcr_logger = DcrLogger.from_env(self.credential)

    @classmethod
    def from_env(cls) -> "FeedsProcessor":
        collections = []
        seen_collection_ids = set()
        for cid, cname in RECOMMENDED_COLLECTIONS.items():
            env_key = f"INCLUDE_{cid}"
            if os.environ.get(env_key, "false").lower() == "true":
                collections.append({"id": cid, "name": cname})
                seen_collection_ids.add(cid)
                logger.info("  Recommended collection enabled: %s", cname)

        custom_ids = os.environ.get("CUSTOM_COLLECTION_IDS", "").strip()
        custom_names = os.environ.get("CUSTOM_COLLECTION_NAMES", "").strip()
        if custom_ids:
            ids = [x.strip() for x in custom_ids.split(",") if x.strip()]
            names = [x.strip() for x in custom_names.split(",")] if custom_names else []
            for i, cid in enumerate(ids):
                if cid in seen_collection_ids:
                    logger.warning("Skipping duplicate custom collection id: %s", cid)
                    continue
                name = names[i] if i < len(names) else f"Custom-Feed-{i + 1}"
                collections.append({"id": cid, "name": name})
                seen_collection_ids.add(cid)
                logger.info("  Custom collection added: %s (%s)", name, cid[:8])

        if not collections:
            logger.warning("  No collections configured!")

        return cls({
            "socradar_api_key": os.environ["SOCRADAR_API_KEY"],
            "workspace_id": os.environ["WORKSPACE_ID"],
            "subscription_id": os.environ["SUBSCRIPTION_ID"],
            "resource_group": os.environ["RESOURCE_GROUP"],
            "workspace_name": os.environ["WORKSPACE_NAME"],
            "storage_account_name": os.environ["STORAGE_ACCOUNT_NAME"],
            "collections": collections,
            "enable_feeds_table": os.environ.get("ENABLE_FEEDS_TABLE", "true").lower() == "true",
            "enable_audit_logging": os.environ.get("ENABLE_AUDIT_LOGGING", "true").lower() == "true",
        })

    def _get_mgmt_token(self) -> str:
        if not self._mgmt_token:
            token = self.credential.get_token("https://management.azure.com/.default")
            self._mgmt_token = token.token
        return self._mgmt_token

    def fetch_feed(self, collection_id: str) -> List[dict]:
        url = f"{SOCRADAR_FEED_URL}/{collection_id}.json?key={self.api_key}&v=2"
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            body = resp.text[:500].replace("\n", " ")
            raise RuntimeError(
                f"Feed API failed for {collection_id}: HTTP {resp.status_code} - {body}"
            )
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    def get_checkpoint(self, collection_id: str) -> str:
        try:
            entity = self.table_client.get_entity(
                partition_key=collection_id, row_key="state"
            )
            return entity.get("LastProcessedDate", "1970-01-01T00:00:00Z")
        except Exception:
            return "1970-01-01T00:00:00Z"

    def save_checkpoint(self, collection_id: str, collection_name: str,
                        total_count: int, new_count: int):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entity = {
            "PartitionKey": collection_id,
            "RowKey": "state",
            "LastProcessedDate": now,
            "CollectionName": collection_name,
            "IndicatorsProcessed": total_count,
            "NewIndicators": new_count,
            "LastRun": now,
        }
        self.table_client.upsert_entity(entity)

    def filter_new_indicators(self, items: List[dict], checkpoint: str) -> List[dict]:
        is_first_run = checkpoint == "1970-01-01T00:00:00Z"
        new_items = []
        for item in items:
            feed_val = (item.get("feed") or "").strip()
            if not feed_val:
                continue
            latest_seen = item.get("latest_seen_date", "")
            if not latest_seen:
                # Match the Logic App behavior: always process undated indicators.
                new_items.append(item)
            elif latest_seen > checkpoint:
                new_items.append(item)
        return new_items

    def upload_batch(self, indicators: List[dict]) -> Tuple[int, int]:
        token = self._get_mgmt_token()
        url = SENTINEL_UPLOAD_URL.format(workspace_id=self.workspace_id)
        url += "?api-version=2022-07-01"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "sourcesystem": "SOCRadar Threat Feeds",
            "indicators": indicators,
        }

        resp = requests.post(url, headers=headers, json=body, timeout=60)

        if resp.status_code == 200:
            result = resp.json() if resp.text else {}
            errors = result.get("errors", [])
            skipped = len(errors)
            created = len(indicators) - skipped
            if errors:
                logger.warning("Upload batch had %d errors: %s", skipped, str(errors[:3])[:500])
            return created, skipped
        else:
            logger.error("Upload failed: %d %s", resp.status_code, resp.text[:500])
            return 0, len(indicators)

    def run(self) -> dict:
        total_created = 0
        total_skipped = 0
        collections_processed = 0
        collection_errors = []

        if not self.collections:
            logger.warning("No collections configured")
            return {"collections_processed": 0, "indicators_created": 0, "indicators_skipped": 0}

        total_collections = len(self.collections)
        for idx, col in enumerate(self.collections, 1):
            try:
                logger.info("Step 2.1: [%d/%d] Processing collection: %s", idx, total_collections, col["name"])

                logger.info("Step 2.2: [%s] Fetching feed from SOCRadar API", col["name"])
                items = self.fetch_feed(col["id"])
                logger.info("Step 2.3: [%s] Fetched %d indicators", col["name"], len(items))

                checkpoint = self.get_checkpoint(col["id"])
                is_first_run = checkpoint == "1970-01-01T00:00:00Z"
                logger.info("Step 2.4: [%s] Checkpoint: %s%s", col["name"], checkpoint,
                            " (first run)" if is_first_run else "")

                new_items = self.filter_new_indicators(items, checkpoint)
                logger.info("Step 2.5: [%s] Filtered: %d new from %d total", col["name"], len(new_items), len(items))

                if not new_items:
                    logger.info("Step 2.5: [%s] No new indicators, saving checkpoint", col["name"])
                    self.save_checkpoint(col["id"], col["name"], len(items), 0)
                    collections_processed += 1
                    continue

                # Build STIX indicators
                logger.info("Step 2.6: [%s] Building STIX indicators", col["name"])
                stix_indicators = []
                feed_logs = []
                for item in new_items:
                    indicator = StixBuilder.build_indicator(item, col)
                    if indicator:
                        stix_indicators.append(indicator)
                        if self.enable_feeds_table:
                            feed_logs.append(StixBuilder.build_feed_log(item, col))
                logger.info("Step 2.6: [%s] Built %d STIX indicators", col["name"], len(stix_indicators))

                # Batch upload to Sentinel TI
                col_created = 0
                col_skipped = 0
                total_batches = (len(stix_indicators) + BATCH_SIZE - 1) // BATCH_SIZE
                for i in range(0, len(stix_indicators), BATCH_SIZE):
                    batch = stix_indicators[i:i + BATCH_SIZE]
                    batch_num = (i // BATCH_SIZE) + 1
                    logger.info("Step 2.7: [%s] Uploading batch %d/%d (%d indicators)",
                                col["name"], batch_num, total_batches, len(batch))
                    created, skipped = self.upload_batch(batch)
                    col_created += created
                    col_skipped += skipped
                    logger.info("Step 2.7: [%s] Batch %d result: %d created, %d skipped",
                                col["name"], batch_num, created, skipped)

                total_created += col_created
                total_skipped += col_skipped
                logger.info("Step 2.8: [%s] Upload complete: %d created, %d skipped",
                            col["name"], col_created, col_skipped)

                # Log to feeds table
                if self.enable_feeds_table and feed_logs and self.dcr_logger:
                    logger.info("Step 2.9: [%s] Logging %d records to feeds table", col["name"], len(feed_logs))
                    self.dcr_logger.log_feeds(feed_logs)

                self.save_checkpoint(col["id"], col["name"], len(items), len(new_items))
                collections_processed += 1
                logger.info("Step 2.10: [%s] Checkpoint saved, collection done", col["name"])

            except Exception as e:
                message = f"{col['name']} ({col['id']}): {e}"
                collection_errors.append(message)
                logger.exception("  ERROR processing %s", message)

        if collection_errors:
            logger.error("Collection failures: %s", " | ".join(collection_errors))
            if collections_processed == 0:
                raise RuntimeError("All configured collections failed: " + " | ".join(collection_errors))

        return {
            "collections_processed": collections_processed,
            "indicators_created": total_created,
            "indicators_skipped": total_skipped,
        }

    def log_audit(self, **kwargs):
        if self.enable_audit_logging and self.dcr_logger:
            self.dcr_logger.log_audit(kwargs)
