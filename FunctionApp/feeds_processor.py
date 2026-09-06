"""
SOCRadar Feeds Processor
Fetches feeds from SOCRadar API, filters by checkpoint, uploads to Sentinel TI in batches.
"""

import os
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests

from stix_builder import StixBuilder, parse_feed_datetime, format_checkpoint
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

# A 429 or 5xx means the request never landed. Retrying it is the difference
# between a delayed indicator and a lost one, so both the fetch and the upload
# retry, bounded, and both honour Retry-After when the server sends it.
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
MAX_RETRY_SLEEP = 60

# The feed returns its whole active list every time, and Sentinel treats an
# upload with a known STIX id as an update. Re-sending everything close to the
# checkpoint is therefore cheap, and the overlap is what catches an entry whose
# latest_seen_date was stamped after the previous run had already read it.
CHECKPOINT_OVERLAP_HOURS = 48
TOKEN_REFRESH_MARGIN_SECONDS = 300
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def redact(text) -> str:
    """The feed API takes the key as a query parameter, and requests puts the
    full URL into its exception messages. Nothing that came near a URL is
    logged or written to a table without passing through here."""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", str(text))


class FeedsProcessor:

    def __init__(self, config: dict):
        self.api_key = config["socradar_api_key"]
        self.workspace_id = config["workspace_id"]
        self.collections = config["collections"]
        self.enable_feeds_table = config.get("enable_feeds_table", False)
        self.enable_audit_logging = config.get("enable_audit_logging", False)
        self.initial_lookback_days = int(config.get("initial_lookback_days", 0) or 0)
        self.time_budget_seconds = int(config.get("time_budget_seconds", 0) or 0)

        self.credential = config.get("credential")
        self.table_client = config.get("table_client")
        self.dcr_logger = config.get("dcr_logger")

        self._mgmt_token = None
        self._mgmt_token_expires = 0
        self._deadline = None

    @classmethod
    def from_env(cls) -> "FeedsProcessor":
        from azure.identity import DefaultAzureCredential
        from azure.data.tables import TableServiceClient

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
                name = names[i] if i < len(names) and names[i] else RECOMMENDED_COLLECTIONS.get(cid, f"Custom-Feed-{i + 1}")
                collections.append({"id": cid, "name": name})
                seen_collection_ids.add(cid)
                logger.info("  Custom collection added: %s (%s)", name, cid[:8])

        if not collections:
            logger.warning("  No collections configured!")

        credential = DefaultAzureCredential()
        storage_account_name = os.environ["STORAGE_ACCOUNT_NAME"]
        table_client = TableServiceClient(
            endpoint=f"https://{storage_account_name}.table.core.windows.net",
            credential=credential,
        ).get_table_client("FeedState")

        enable_feeds_table = os.environ.get("ENABLE_FEEDS_TABLE", "true").lower() == "true"
        enable_audit_logging = os.environ.get("ENABLE_AUDIT_LOGGING", "true").lower() == "true"
        dcr_logger = DcrLogger.from_env(credential) if (enable_feeds_table or enable_audit_logging) else None

        return cls({
            "socradar_api_key": os.environ["SOCRADAR_API_KEY"],
            "workspace_id": os.environ["WORKSPACE_ID"],
            "collections": collections,
            "enable_feeds_table": enable_feeds_table,
            "enable_audit_logging": enable_audit_logging,
            "initial_lookback_days": os.environ.get("INITIAL_LOOKBACK_DAYS", "0"),
            "time_budget_seconds": os.environ.get("TIME_BUDGET_SECONDS", str(9 * 60)),
            "credential": credential,
            "table_client": table_client,
            "dcr_logger": dcr_logger,
        })

    # ------------------------------------------------------------------ auth

    def _get_mgmt_token(self) -> str:
        now = time.time()
        if not self._mgmt_token or self._mgmt_token_expires - now < TOKEN_REFRESH_MARGIN_SECONDS:
            token = self.credential.get_token("https://management.azure.com/.default")
            self._mgmt_token = token.token
            self._mgmt_token_expires = getattr(token, "expires_on", now + 3600)
        return self._mgmt_token

    # --------------------------------------------------------- time budget

    def _remaining(self) -> Optional[float]:
        if self._deadline is None:
            return None
        return self._deadline - time.time()

    def _sleep_before_retry(self, resp, attempt, what) -> bool:
        """Sleep before the next attempt. False means stop retrying.

        Stops on a non-retryable status, on the last attempt, and when the wait
        would run past the collection's time budget. The host kills the
        function at its own timeout without writing an audit row, so giving up
        early and reporting the failure beats sleeping into a silent kill.
        """
        if resp.status_code not in RETRYABLE_STATUS or attempt >= MAX_ATTEMPTS:
            return False

        retry_after = resp.headers.get("Retry-After", "")
        try:
            wait = min(float(retry_after), MAX_RETRY_SLEEP)
        except (TypeError, ValueError):
            wait = min(2 ** attempt, MAX_RETRY_SLEEP)

        remaining = self._remaining()
        if remaining is not None and wait >= remaining:
            logger.warning("%s got HTTP %d but the %.0fs wait exceeds the remaining budget",
                           what, resp.status_code, wait)
            return False

        logger.warning("%s got HTTP %d, retrying in %.0fs (attempt %d/%d)",
                       what, resp.status_code, wait, attempt, MAX_ATTEMPTS)
        time.sleep(wait)
        return True

    # ---------------------------------------------------------------- feed

    def fetch_feed(self, collection_id: str) -> List[dict]:
        url = f"{SOCRADAR_FEED_URL}/{collection_id}.json"
        params = {"key": self.api_key, "v": "2"}
        resp = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.get(url, params=params, timeout=60)
            except Exception as e:
                # requests embeds the full URL, key included, in its message.
                raise RuntimeError(
                    f"Feed API request failed for {collection_id}: {redact(e)}"
                ) from None
            if resp.status_code == 200:
                break
            if not self._sleep_before_retry(resp, attempt, "Feed API"):
                break

        if resp.status_code != 200:
            body = redact(resp.text[:500].replace("\n", " "))
            raise RuntimeError(f"Feed API failed for {collection_id}: HTTP {resp.status_code} - {body}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Feed API returned a non-JSON body for {collection_id}") from None
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        # A 200 with an error object is not an empty feed. Treating it as one
        # would record "no new indicators" and move the checkpoint past data
        # that was never seen.
        raise RuntimeError(
            f"Feed API returned an unexpected body for {collection_id}: {redact(str(data)[:200])}"
        )

    # ---------------------------------------------------------- checkpoint

    @staticmethod
    def _is_not_found(exc) -> bool:
        name = type(exc).__name__
        return (isinstance(exc, KeyError) or "NotFound" in name
                or getattr(exc, "status_code", None) == 404)

    def get_checkpoint(self, collection_id: str) -> Optional[datetime]:
        """The instant up to which this collection has been delivered, or None
        before the first successful run. Any error other than "no row yet" is
        raised: a permission problem read as a first run would re-import the
        whole feed and hide the misconfiguration."""
        try:
            entity = self.table_client.get_entity(partition_key=collection_id, row_key="state")
        except Exception as e:
            if self._is_not_found(e):
                return None
            raise RuntimeError(f"Checkpoint read failed for {collection_id}: {redact(e)}") from None
        return parse_feed_datetime(entity.get("LastProcessedDate", ""))

    def save_checkpoint(self, collection_id: str, collection_name: str,
                        processed_through: datetime, total_count: int, new_count: int,
                        failed_count: int = 0):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.table_client.upsert_entity({
            "PartitionKey": collection_id,
            "RowKey": "state",
            "LastProcessedDate": format_checkpoint(processed_through),
            "CollectionName": collection_name,
            "IndicatorsProcessed": total_count,
            "NewIndicators": new_count,
            "IndicatorsFailed": failed_count,
            "LastRun": now,
        })

    def window_start(self, checkpoint: Optional[datetime]) -> Optional[datetime]:
        """Oldest latest_seen_date this run will send. None means everything."""
        if checkpoint is not None:
            return checkpoint - timedelta(hours=CHECKPOINT_OVERLAP_HOURS)
        if self.initial_lookback_days > 0:
            return datetime.now(timezone.utc) - timedelta(days=self.initial_lookback_days)
        return None

    def filter_new_indicators(self, items: List[dict], checkpoint: Optional[datetime]) -> Tuple[List[dict], dict]:
        window = self.window_start(checkpoint)
        new_items = []
        stats = {"empty": 0, "undated": 0}
        for item in items:
            if not (item.get("feed") or "").strip():
                stats["empty"] += 1
                continue
            seen = parse_feed_datetime(item.get("latest_seen_date"))
            if seen is None:
                # No date to compare. Sending it again is an update, not a
                # duplicate, so undated entries always go.
                stats["undated"] += 1
                new_items.append(item)
            elif window is None or seen >= window:
                new_items.append(item)
        return new_items, stats

    # -------------------------------------------------------------- upload

    def upload_batch(self, indicators: List[dict]) -> Tuple[int, int, int]:
        """Upload one batch to Sentinel TI.

        Returns (created, skipped, failed). The last two are not the same
        thing. Skipped indicators came back inside a successful response:
        Sentinel read them and rejected them, so sending them again changes
        nothing. Failed indicators never reached Sentinel at all, and the
        caller must keep the checkpoint where it is so the next run sends
        them again.
        """
        url = SENTINEL_UPLOAD_URL.format(workspace_id=self.workspace_id) + "?api-version=2022-07-01"
        body = {"sourcesystem": "SOCRadar Threat Feeds", "indicators": indicators}
        resp = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            headers = {
                "Authorization": f"Bearer {self._get_mgmt_token()}",
                "Content-Type": "application/json",
            }
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=60)
            except Exception as e:
                logger.error("Upload request failed: %s", redact(e))
                return 0, 0, len(indicators)
            if resp.status_code == 200:
                result = resp.json() if resp.text else {}
                errors = result.get("errors", []) if isinstance(result, dict) else []
                skipped = len(errors)
                created = len(indicators) - skipped
                if errors:
                    logger.warning("Upload batch had %d rejected indicator(s): %s",
                                   skipped, str(errors[:3])[:500])
                return created, skipped, 0
            if not self._sleep_before_retry(resp, attempt, "Sentinel upload"):
                break

        logger.error("Upload failed after %d attempt(s): %d %s",
                     attempt, resp.status_code, resp.text[:500])
        return 0, 0, len(indicators)

    # ----------------------------------------------------------------- run

    def process_collection(self, col: dict) -> dict:
        name, cid = col["name"], col["id"]
        result = {
            "collection": name, "created": 0, "skipped": 0, "failed": 0,
            "unsupported": 0, "fetched": 0, "selected": 0, "feeds_table_ok": True,
        }

        logger.info("Step 2.2: [%s] Fetching feed from SOCRadar API", name)
        items = self.fetch_feed(cid)
        result["fetched"] = len(items)
        logger.info("Step 2.3: [%s] Fetched %d indicators", name, len(items))

        checkpoint = self.get_checkpoint(cid)
        window = self.window_start(checkpoint)
        logger.info("Step 2.4: [%s] Checkpoint: %s, window from %s", name,
                    format_checkpoint(checkpoint) if checkpoint else "none (first run)",
                    format_checkpoint(window) if window else "the beginning")

        new_items, stats = self.filter_new_indicators(items, checkpoint)
        result["selected"] = len(new_items)
        logger.info("Step 2.5: [%s] Selected %d of %d (%d undated, %d empty)",
                    name, len(new_items), len(items), stats["undated"], stats["empty"])

        # Where the checkpoint goes if nothing is lost: the newest sighting
        # this run delivered. It never moves backwards, and on a first run
        # with nothing to send it pins the window this run started from.
        newest = max((d for d in (parse_feed_datetime(i.get("latest_seen_date")) for i in new_items) if d),
                     default=None)
        pinned = checkpoint or window or EPOCH
        advance_to = max(pinned, newest) if newest else pinned

        if not new_items:
            self.save_checkpoint(cid, name, advance_to, len(items), 0)
            logger.info("Step 2.5: [%s] Nothing to send, checkpoint kept at %s", name, format_checkpoint(advance_to))
            return result

        stix_indicators, feed_logs = [], []
        for item in new_items:
            indicator = StixBuilder.build_indicator(item, col)
            if not indicator:
                result["unsupported"] += 1
                continue
            stix_indicators.append(indicator)
            if self.enable_feeds_table:
                feed_logs.append(StixBuilder.build_feed_log(item, col))
        if result["unsupported"]:
            logger.warning("Step 2.6: [%s] %d indicator(s) have an unsupported type or hash length and were not sent",
                           name, result["unsupported"])
        logger.info("Step 2.6: [%s] Built %d STIX indicators", name, len(stix_indicators))

        total_batches = (len(stix_indicators) + BATCH_SIZE - 1) // BATCH_SIZE
        delivered_logs = []
        for i in range(0, len(stix_indicators), BATCH_SIZE):
            batch = stix_indicators[i:i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            remaining = self._remaining()
            if remaining is not None and remaining <= 0:
                # Out of time. Everything not yet sent counts as failed so the
                # checkpoint stays put and the next run picks it up.
                result["failed"] += len(stix_indicators) - i
                logger.error("Step 2.7: [%s] Time budget exhausted before batch %d/%d; %d indicator(s) deferred to the next run",
                             name, batch_num, total_batches, len(stix_indicators) - i)
                break
            logger.info("Step 2.7: [%s] Uploading batch %d/%d (%d indicators)", name, batch_num, total_batches, len(batch))
            created, skipped, failed = self.upload_batch(batch)
            result["created"] += created
            result["skipped"] += skipped
            if failed:
                # A batch that never landed must not move the checkpoint, and
                # a later batch would bury the gap, so stop here.
                result["failed"] += failed + (len(stix_indicators) - i - len(batch))
                logger.error("Step 2.7: [%s] Batch %d/%d never reached Microsoft Sentinel; holding the checkpoint at %s",
                             name, batch_num, total_batches, format_checkpoint(pinned))
                break
            delivered_logs.extend(feed_logs[i:i + BATCH_SIZE])
            logger.info("Step 2.7: [%s] Batch %d result: %d created, %d skipped", name, batch_num, created, skipped)

        if self.enable_feeds_table and delivered_logs and self.dcr_logger:
            logger.info("Step 2.9: [%s] Logging %d records to feeds table", name, len(delivered_logs))
            result["feeds_table_ok"] = self.dcr_logger.log_feeds(delivered_logs)
            if not result["feeds_table_ok"]:
                logger.error("Step 2.9: [%s] Feeds table ingestion failed; indicators were delivered to Microsoft Sentinel but SOCRadar_Feeds_CL is missing them", name)

        if result["failed"]:
            self.save_checkpoint(cid, name, pinned, len(items), result["created"], result["failed"])
        else:
            self.save_checkpoint(cid, name, advance_to, len(items), result["created"])
            logger.info("Step 2.10: [%s] Checkpoint advanced to %s", name, format_checkpoint(advance_to))
        return result

    def run(self) -> dict:
        totals = {
            "collections_processed": 0, "collections_partial": 0, "collections_failed": 0,
            "indicators_created": 0, "indicators_skipped": 0, "indicators_failed": 0,
            "indicators_unsupported": 0, "errors": [],
        }
        if not self.collections:
            logger.warning("No collections configured")
            return totals

        n = len(self.collections)
        budget = (self.time_budget_seconds / n) if self.time_budget_seconds > 0 else 0
        for idx, col in enumerate(self.collections, 1):
            logger.info("Step 2.1: [%d/%d] Processing collection: %s", idx, n, col["name"])
            self._deadline = (time.time() + budget) if budget else None
            try:
                r = self.process_collection(col)
            except Exception as e:
                message = f"{col['name']} ({col['id']}): {redact(e)}"
                totals["collections_failed"] += 1
                totals["errors"].append(message)
                logger.error("  ERROR processing %s", message, exc_info=not isinstance(e, RuntimeError))
                continue
            totals["indicators_created"] += r["created"]
            totals["indicators_skipped"] += r["skipped"]
            totals["indicators_failed"] += r["failed"]
            totals["indicators_unsupported"] += r["unsupported"]
            if r["failed"] or not r["feeds_table_ok"]:
                totals["collections_partial"] += 1
                what = (f"{r['failed']} indicator(s) did not reach Microsoft Sentinel and will be sent again on the next run"
                        if r["failed"] else "feeds table ingestion failed")
                totals["errors"].append(f"{col['name']}: {what}")
            else:
                totals["collections_processed"] += 1
        self._deadline = None

        if totals["errors"]:
            logger.error("Collection problems: %s", " | ".join(totals["errors"]))
        if totals["collections_failed"] == n:
            raise RuntimeError("All configured collections failed: " + " | ".join(totals["errors"]))
        return totals

    def log_audit(self, **kwargs) -> bool:
        if self.enable_audit_logging and self.dcr_logger:
            return self.dcr_logger.log_audit(kwargs)
        return True
