"""
STIX 2.1 indicator builder for SOCRadar feed items.
Handles type detection, hash length detection, and pattern construction.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

# Sentinel keys an indicator on its STIX id. A stable id turns every re-upload of
# the same feed entry into an update of one record; a fresh uuid4 per run made
# every re-upload a new record, so a re-seen IOC piled up copies.
ID_NAMESPACE = uuid.UUID("6b1f1d1e-3c6a-4f7e-9b0a-2c3d4e5f6a7b")

# An indicator that is still listed by the feed is still live. Validity is
# therefore counted from the upload, not from latest_seen_date: counting from
# an old sighting produced valid_until dates already in the past.
VALIDITY_DAYS = 90

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",      # what the feed API returns
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",     # what the checkpoint stores
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d",
)


def parse_feed_datetime(value) -> Optional[datetime]:
    """Parse a feed or checkpoint timestamp. None when absent or unreadable.

    The feed sends naive timestamps; they are treated as UTC. Comparing them as
    datetimes instead of strings is what makes "YYYY-MM-DD HH:MM:SS" and
    "YYYY-MM-DDTHH:MM:SSZ" comparable at all.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def format_checkpoint(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_stix_type(feed_type: str) -> Optional[str]:
    ft = (feed_type or "").lower().strip()
    if ft in ("ip", "ipv4-addr", "ipv4"):
        return "ipv4-addr"
    if ft in ("ipv6", "ipv6-addr"):
        return "ipv6-addr"
    if ft in ("domain", "hostname", "domain-name"):
        return "domain-name"
    if ft in ("hash", "file", "md5", "sha1", "sha256"):
        return "file"
    if ft == "url":
        return "url"
    if ft in ("email", "email-addr"):
        return "email-addr"
    # Unknown types are reported by the caller, not silently mapped to a
    # domain pattern that would match nothing or the wrong thing.
    return None


def _escape(value: str) -> str:
    # STIX pattern string literals: backslash and single quote are the two
    # characters that end or corrupt the literal.
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_pattern(stix_type: str, value: str) -> Optional[str]:
    v = _escape(value)
    if stix_type == "file":
        vlen = len(value)
        if vlen == 64:
            return f"[file:hashes.'SHA-256' = '{v}']"
        if vlen == 40:
            return f"[file:hashes.'SHA-1' = '{v}']"
        if vlen == 32:
            return f"[file:hashes.MD5 = '{v}']"
        return None
    return f"[{stix_type}:value = '{v}']"


def _resolve_threat_type(collection_name: str) -> str:
    name = collection_name.lower()
    if "phishing" in name:
        return "Phishing"
    if "attacker" in name:
        return "Malicious-Activity"
    return "Malware"


class StixBuilder:

    @staticmethod
    def indicator_id(stix_type: str, value: str, collection_id: str) -> str:
        return "indicator--" + str(uuid.uuid5(ID_NAMESPACE, f"{stix_type}|{value}|{collection_id}"))

    @staticmethod
    def build_indicator(item: dict, collection: dict) -> Optional[dict]:
        """Return a STIX indicator, or None when the item cannot be expressed.

        None means the feed type or hash length is not supported. The caller
        counts those; they are not upload errors.
        """
        feed_val = (item.get("feed") or "").strip()
        if not feed_val:
            return None

        stix_type = _resolve_stix_type(item.get("feed_type", ""))
        if not stix_type:
            return None
        pattern = _build_pattern(stix_type, feed_val)
        if not pattern:
            return None
        threat_type = _resolve_threat_type(collection["name"])

        now = datetime.now(timezone.utc)
        seen = parse_feed_datetime(item.get("latest_seen_date"))
        valid_from = seen if seen and seen < now else now
        valid_until = now + timedelta(days=VALIDITY_DAYS)

        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        return {
            "type": "indicator",
            "spec_version": "2.1",
            "id": StixBuilder.indicator_id(stix_type, feed_val, collection["id"]),
            "created": valid_from.strftime(fmt),
            "modified": now.strftime(fmt),
            "name": f"{stix_type} - {feed_val[:100]}",
            "description": f"SOCRadar feed: {collection['name']}",
            "pattern": pattern,
            "pattern_type": "stix",
            "valid_from": valid_from.strftime(fmt),
            "valid_until": valid_until.strftime(fmt),
            "confidence": 80,
            "labels": ["SOCRadar", "Feeds", collection["name"]],
            "indicator_types": [threat_type],
        }

    @staticmethod
    def build_feed_log(item: dict, collection: dict) -> dict:
        return {
            "TimeGenerated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "CollectionName": collection["name"],
            "CollectionUUID": collection["id"],
            "IndicatorValue": (item.get("feed") or "")[:500],
            "IndicatorType": item.get("feed_type", "unknown"),
            "LatestSeenDate": item.get("latest_seen_date", ""),
            "Source": "SOCRadar Threat Feeds",
        }
