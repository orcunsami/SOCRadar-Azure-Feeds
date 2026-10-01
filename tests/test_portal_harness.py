#!/usr/bin/env python3
"""scripts/portal_test.sh must be able to fail, and to pass.

The harness once read a status key the host never sends, counted indicators
through a 1000-row page, and compared two capped sets for "dedup", so it could
not pass on a healthy deployment and said nothing about a broken one. Here it
runs against a fake `az`/`curl` that plays a product, and each broken product
must come out FAIL while the healthy one comes out PASS.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "portal_test.sh")

FAKE = r'''
import json, os, re, sys
S = os.environ["FAKE_STATE"]
scenario = os.environ["FAKE_SCENARIO"]
st = json.load(open(S))

def save():
    json.dump(st, open(S, "w"))

def run_import():
    n = st["runs"]; st["runs"] += 1
    if scenario == "noaudit":
        return
    if n == 0:
        ids = list(range(3882)); created = 3882; rows = 3907
    else:
        created = 2049; rows = 2049
        ids = list(range(10000 * n, 10000 * n + 2049)) if scenario in ("uuid4", "uuid4_delayed") else list(range(2049))
    status, failed = "Success", 0
    if scenario == "failed":
        status, failed, created, rows, ids = "Failed", 0, 0, 0, []
    if scenario == "partial":
        status, failed = "PartialSuccess", 5
    if scenario == "nosend2" and n > 0:
        created, rows, ids = 0, 0, []
    if scenario == "nosend":
        created, rows, ids = 0, 0, []
    st["audit"].append([status, created, failed])
    delay = 3 if (n > 0 and scenario in ("uuid4_delayed", "healthy_delayed")) else 0
    st["ti"].append({"rows": rows, "ids": ids, "delay": delay})

def visible():
    return [r for r in st["ti"] if r["delay"] <= 0]

def query(q):
    if "SOCRadar_Feeds_Audit_CL | count" in q:
        return [[len(st["audit"])]]
    if "top 1 by TimeGenerated" in q:
        return [st["audit"][-1]] if st["audit"] else []
    if "ThreatIntelIndicators" in q and "distinct Id" in q:
        return [[len({i for r in visible() for i in r["ids"]})]]
    if "ThreatIntelIndicators" in q and q.rstrip().endswith("| count"):
        total = sum(r["rows"] for r in visible())
        for r in st["ti"]:
            r["delay"] -= 1
        return [[total]]
    raise SystemExit("unexpected query: " + q)

tool, args = sys.argv[1], sys.argv[2:]
line = " ".join(args)
if tool == "curl":
    run_import(); save(); print("202"); sys.exit(0)
if args[:2] == ["rest", "--method"] and "/api/query" in line:
    body = json.loads(args[args.index("--body") + 1])
    rows = query(body["query"]); save()
    print(json.dumps({"tables": [{"columns": [], "rows": rows}]})); sys.exit(0)
if "rest" == args[0]:
    sys.exit("harness must not use ARM TI paging: " + line)
if line.startswith("account show") and "user.name" in line: print("tester")
elif line.startswith("account show"): print("sub-1")
elif line.startswith("functionapp list"): print("socradar-feeds-test")
elif line.startswith("functionapp show"): print("Running")
elif line.startswith("functionapp keys"): print("KEY")
elif line.startswith("identity show"): print("principal")
elif line.startswith("role assignment"): print("Microsoft Sentinel Contributor")
elif line.startswith("storage account list"): print("srfeedstest")
elif line.startswith("storage table list"): print("FeedState")
elif line.startswith("storage entity query") and "length" in line: print("3")
save()
'''


def play(scenario, script=SCRIPT):
    tmp = tempfile.mkdtemp(prefix="portal-harness-")
    try:
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(script, os.path.join(tmp, "scripts", "portal_test.sh"))
        fake = os.path.join(tmp, "fake.py")
        open(fake, "w").write(FAKE)
        state = os.path.join(tmp, "state.json")
        json.dump({"runs": 0, "audit": [], "ti": []}, open(state, "w"))
        for name, body in (("az", 'exec python3 "%s" az "$@"' % fake), ("curl", 'exec python3 "%s" curl "$@"' % fake),
                           ("sleep", "exit 0")):
            path = os.path.join(bin_dir, name)
            open(path, "w").write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_STATE=state,
                   FAKE_SCENARIO=scenario, SUBSCRIPTION_ID="sub-1", RESOURCE_GROUP="rg", WORKSPACE_NAME="ws")
        r = subprocess.run(["bash", os.path.join(tmp, "scripts", "portal_test.sh")], env=env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def row(out, name):
    for line in out.splitlines():
        if line.startswith("| " + name):
            return line.split("|")[2].strip()
    return None


# A healthy deployment (3882 ids, more than any one page) passes every row.
rc, out = play("healthy")
check(rc == 0 and "RESULT: PASS" in out, "healthy product did not pass (rc=%s)\n%s" % (rc, out[-900:]))
check(row(out, "TI Indicators") == "PASS (3882 ids)", "TI row: %r" % row(out, "TI Indicators"))
# Late ingestion of the second run's rows must not turn it red either.
rc, out = play("healthy_delayed")
check(rc == 0, "healthy product with late ingestion did not pass\n%s" % out[-900:])

# Broken products must not pass.
rc, out = play("uuid4")
check(rc != 0 and row(out, "Checkpoint Dedup") == "FAIL (ids grew)", "ids that grow on every run read as dedup: %r" % row(out, "Checkpoint Dedup"))
rc, out = play("uuid4_delayed")
check(rc != 0 and row(out, "Checkpoint Dedup") == "FAIL (ids grew)",
      "dedup was judged before the second run's rows landed: %r" % row(out, "Checkpoint Dedup"))
rc, out = play("noaudit")
check(rc != 0 and row(out, "Import Run") == "TIMEOUT" and row(out, "Second Run") == "TIMEOUT",
      "a run that never wrote its audit row did not time out: %r / %r" % (row(out, "Import Run"), row(out, "Second Run")))
rc, out = play("failed")
check(rc != 0 and (row(out, "Import Run") or "").startswith("FAIL"), "a Failed run passed: %r" % row(out, "Import Run"))
rc, out = play("partial")
check(rc != 0 and (row(out, "Import Run") or "").startswith("FAIL"), "a PartialSuccess run passed: %r" % row(out, "Import Run"))
rc, out = play("nosend")
check(rc != 0 and (row(out, "TI Indicators") or "").startswith("FAIL"), "a run that imported nothing passed: %r" % row(out, "TI Indicators"))
rc, out = play("nosend2")
check(rc != 0 and row(out, "Checkpoint Dedup") == "CANNOT-MEASURE",
      "a second run that sent nothing proved dedup: %r" % row(out, "Checkpoint Dedup"))

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("portal_test.sh passes a healthy product and fails broken ones: OK")
