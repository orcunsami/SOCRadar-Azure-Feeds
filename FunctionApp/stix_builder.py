"""
STIX 2.1 indicator builder for SOCRadar feed items.
Handles type detection, hash length detection, and pattern construction.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional


def _resolve_stix_type(feed_type: str) -> str:
    ft = (feed_type or "").lower().strip()
    if ft in ("ip", "ipv4-addr", "ipv4"):
        return "ipv4-addr"
    if ft in ("domain", "hostname", "domain-name"):
        return "domain-name"
    if ft in ("hash", "file", "md5", "sha1", "sha256"):
        return "file"
    if ft == "url":
        return "url"
    if ft in ("email", "email-addr"):
        return "email-addr"
    return "domain-name"


def _build_pattern(stix_type: str, value: str) -> str:
    if stix_type == "ipv4-addr":
        return f"[ipv4-addr:value = '{value}']"
    if stix_type == "domain-name":
        return f"[domain-name:value = '{value}']"
    if stix_type == "url":
        return f"[url:value = '{value}']"
    if stix_type == "email-addr":
        return f"[email-addr:value = '{value}']"
    if stix_type == "file":
        vlen = len(value)
        if vlen == 64:
            return f"[file:hashes.'SHA-256' = '{value}']"
        elif vlen == 40:
            return f"[file:hashes.'SHA-1' = '{value}']"
        else:
            return f"[file:hashes.MD5 = '{value}']"
    return f"[domain-name:value = '{value}']"


def _resolve_threat_type(collection_name: str) -> str:
    name = collection_name.lower()
    if "phishing" in name:
        return "Phishing"
    if "attacker" in name:
        return "Malicious-Activity"
    return "Malware"


def _parse_datetime(dt_str: str) -> datetime:
    if not dt_str:
        return datetime.now(timezone.utc)
    # SOCRadar format: "2026-02-20 10:00:00" or ISO format
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(dt_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


class StixBuilder:

    @staticmethod
    def build_indicator(item: dict, collection: dict) -> Optional[dict]:
        feed_val = (item.get("feed") or "").strip()
        if not feed_val:
            return None

        feed_type = item.get("feed_type", "")
        stix_type = _resolve_stix_type(feed_type)
        pattern = _build_pattern(stix_type, feed_val)
        threat_type = _resolve_threat_type(collection["name"])

        latest_seen = item.get("latest_seen_date", "")
        now = datetime.now(timezone.utc)

        valid_from_dt = _parse_datetime(latest_seen) if latest_seen else now
        valid_until_dt = valid_from_dt + timedelta(days=90)

        now_str = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        valid_from_str = valid_from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        valid_until_str = valid_until_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        return {
            "type": "indicator",
            "spec_version": "2.1",
            "id": f"indicator--{uuid.uuid4()}",
            "created": now_str,
            "modified": now_str,
            "name": f"{stix_type} - {feed_val[:100]}",
            "description": f"SOCRadar feed: {collection['name']}",
            "pattern": pattern,
            "pattern_type": "stix",
            "valid_from": valid_from_str,
            "valid_until": valid_until_str,
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
