#!/usr/bin/env python3
"""Send sample events to the native ASIM tables so the Threat Feeds hunting rules can be tried.

Test only, not part of the product. Deploy scripts/e2e-asim-dcr.json into the workspace's
resource group first, give the signed-in user Monitoring Metrics Publisher on the DCR it
creates, then:

    python3 scripts/e2e-asim-send.py <dceEndpoint> <dcrImmutableId> <ip> <domain> <url> <sha256|sha1|md5 hash>

Each of the five tables gets one event carrying the value you pass, plus one event with a
value no feed contains. Needs `az login`.
"""

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

STREAMS = {
    "Custom-E2ENetwork": "network",
    "Custom-E2EDns": "dns",
    "Custom-E2EWeb": "web",
    "Custom-E2EFile": "file",
    "Custom-E2EProcess": "process",
}


def token():
    out = subprocess.run(["az", "account", "get-access-token", "--resource", "https://monitor.azure.com",
                          "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


# The unifying parsers (_Im_Dns, _Im_ProcessCreate, ...) filter on EventType, so a made-up value is invisible to them.
EVENT_TYPES = {"network": "EndpointNetworkSession", "dns": "Query", "web": "HTTPsession", "file": "FileCreated", "process": "ProcessCreated"}


def base(host, kind):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"TimeGenerated": now, "EventStartTime": now, "EventEndTime": now, "EventCount": 1,
            "EventType": EVENT_TYPES[kind], "EventResult": "Success", "EventSeverity": "Informational",
            "EventProduct": "SOCRadar E2E", "EventVendor": "SOCRadar", "EventSchemaVersion": "0.1.0",
            "DvcHostname": host}


def events(ip, domain, url, hash_value):
    sha256 = hash_value if len(hash_value) == 64 else ""
    sha1 = hash_value if len(hash_value) == 40 else ""
    md5 = hash_value if len(hash_value) == 32 else ""
    out = {k: [] for k in STREAMS}
    for host, i, d, u, h in (("e2e-host-hit", ip, domain, url, (sha256, sha1, md5)),
                             ("e2e-host-miss", "192.0.2.77", "no-such-indicator.example", "http://no-such-indicator.example/x", ("0" * 64, "0" * 40, "0" * 32))):
        out["Custom-E2ENetwork"].append({**base(host, "network"), "SrcIpAddr": "10.0.0.5", "DstIpAddr": i})
        out["Custom-E2EDns"].append({**base(host, "dns"), "SrcIpAddr": "10.0.0.5", "DnsQuery": d})
        out["Custom-E2EWeb"].append({**base(host, "web"), "SrcIpAddr": "10.0.0.5", "Url": u})
        out["Custom-E2EFile"].append({**base(host, "file"), "TargetFilePath": "C:\\e2e\\sample.bin", "TargetFileSHA256": h[0], "TargetFileSHA1": h[1], "TargetFileMD5": h[2]})
        out["Custom-E2EProcess"].append({**base(host, "process"), "TargetProcessName": "C:\\e2e\\sample.exe", "TargetProcessSHA256": h[0], "TargetProcessSHA1": h[1], "TargetProcessMD5": h[2]})
    return out


def main(argv):
    if len(argv) != 7:
        print(__doc__)
        return 2
    dce, dcr, ip, domain, url, hash_value = argv[1:]
    tok = token()
    for stream, rows in events(ip, domain, url, hash_value).items():
        req = urllib.request.Request("%s/dataCollectionRules/%s/streams/%s?api-version=2023-01-01" % (dce, dcr, stream),
                                     data=json.dumps(rows).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print(stream, r.status, len(rows), "events")
        except urllib.error.HTTPError as e:
            print(stream, e.code, e.read().decode()[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
