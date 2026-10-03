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
from concurrent.futures import ThreadPoolExecutor

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
    if scenario == "rowsshort" and n == 0:
        rows, ids = 3000, list(range(3000))   # fewer rows than the run says it created
    if scenario == "successfailed":
        failed = 5                            # Success status next to failed indicators
    st["audit"].append([status, created, failed])
    delay = 3 if (n > 0 and scenario in ("uuid4_delayed", "healthy_delayed")) else 0
    st["ti"].append({"rows": rows, "ids": ids, "delay": delay})

def visible():
    return [r for r in st["ti"] if r["delay"] <= 0]

def query(q):
    if "SOCRadar_Feeds_Audit_CL | count" in q:
        if scenario == "auditcountna" or (scenario == "auditbeforena" and st["runs"] == 0):
            raise SystemExit("ERROR: query refused")
        return [[len(st["audit"])]]
    if "top 1 by TimeGenerated" in q:
        if scenario == "auditreadna":
            raise SystemExit("ERROR: query refused")
        return [st["audit"][-1]] if st["audit"] else []
    if "ThreatIntelIndicators" in q and "distinct Id" in q:
        if scenario == "tidistna":
            raise SystemExit("ERROR: query refused")
        return [[len({i for r in visible() for i in r["ids"]})]]
    if "ThreatIntelIndicators" in q and q.rstrip().endswith("| count"):
        if scenario == "tirows1na" and st["runs"] >= 1:
            raise SystemExit("ERROR: query refused")        # unreadable once run 1 was triggered
        if scenario == "tirowsna" and st["runs"] >= 2:
            raise SystemExit("ERROR: query refused")        # unreadable after run 2 was triggered
        total = sum(r["rows"] for r in visible())
        for r in st["ti"]:
            r["delay"] -= 1
        return [[total]]
    raise SystemExit("unexpected query: " + q)

tool, args = sys.argv[1], sys.argv[2:]
line = " ".join(args)
with open(S + ".calls", "a") as f:
    f.write(tool + " " + line + "\n")
if tool == "curl":
    st["curl_n"] = st.get("curl_n", 0) + 1
    save()
    if scenario == "curlfail": print("000"); sys.exit(7)
    if scenario == "curl500": print("500"); sys.exit(0)
    if scenario == "curl503": print("503"); sys.exit(0)
    if scenario == "curl403": print("403"); sys.exit(0)
    if scenario == "curl503once" and st["curl_n"] % 2 == 1: print("503"); sys.exit(0)
    # The key reaches curl only through a header file (-H @file), never argv.
    hdr = None
    for i, a in enumerate(args[:-1]):
        if a == "-H" and args[i + 1].startswith("@"):
            hdr = open(args[i + 1][1:]).read()
            with open(S + ".calls", "a") as f:
                f.write("hdrfile-mode %o\n" % (os.stat(args[i + 1][1:]).st_mode & 0o777))
    if hdr is None or hdr.strip() != "x-functions-key: " + os.environ["FAKE_KEY"]:
        print("401"); sys.exit(0)
    run_import(); save(); print("202"); sys.exit(0)
if args[:2] == ["rest", "--method"] and "/api/query" in line:
    body = json.loads(args[args.index("--body") + 1])
    rows = query(body["query"]); save()
    if scenario.endswith("_lower"):
        out = {"tables": [{"columns": [], "rows": rows}]}
    else:
        # The live 2017-01-01-preview endpoint capitalises its keys.
        out = {"Tables": [{"TableName": "Table_0", "Columns": [], "Rows": rows}]}
    print(json.dumps(out)); sys.exit(0)
if "rest" == args[0]:
    sys.exit("harness must not use ARM TI paging: " + line)
# Failing az calls look like the live CLI: ERROR on stderr, exit 1, nothing on stdout.
if line.startswith("account show") and "user.name" in line:
    if scenario == "notlogged": sys.exit("ERROR: Please run 'az login' to setup account.")
    print("tester")
elif line.startswith("account show"):
    if scenario == "subfail": sys.exit("ERROR: account show refused")
    print("sub-OTHER" if scenario == "wrongsub" else "sub-1")
elif line.startswith("functionapp list"):
    if scenario == "falistfail": sys.exit("ERROR: functionapp list refused")
    if scenario != "nofa": print("socradar-feeds-test")
elif line.startswith("functionapp show"):
    if scenario == "fashowfail": sys.exit("ERROR: show refused")                # rc 1: could not look
    if scenario == "fagone": sys.exit(3)                                        # rc 3: ResourceNotFound
    if scenario == "fashowempty": sys.exit(0)                                   # rc 0, no state
    if scenario == "stopreadfail" and st.get("stopped"): sys.exit("ERROR: show refused")
    stopped = st.get("stopped") and scenario != "stuckrunning"
    print("Stopped" if stopped or scenario in ("startfail", "startok") else "Running")
elif line.startswith("functionapp stop"):
    st["stopped"] = True                                   # the app is stopped even when az says it failed
    if scenario == "stopfail": save(); sys.exit("ERROR: stop refused")
elif line.startswith("functionapp start"):
    if scenario == "startfail": sys.exit("ERROR: start refused")
elif line.startswith("functionapp keys"):
    if scenario == "keyfail": sys.exit("ERROR: keys list refused")
    if scenario != "keyempty": print(os.environ["FAKE_KEY"])
elif line.startswith("identity show"):
    if scenario == "idfail": sys.exit("ERROR: identity show refused")
    if scenario != "idempty": print("principal")
elif line.startswith("role assignment"):
    if scenario == "rolefail": sys.exit("ERROR: role assignment list refused")
    if scenario != "norole": print("Microsoft Sentinel Contributor")
elif line.startswith("storage account list"):
    if scenario == "salistfail": sys.exit("ERROR: storage account list refused")
    if scenario != "nosa": print("srfeedstest")
elif line.startswith("storage table list") or line.startswith("storage entity query"):
    # Live az 2.84: without --auth-mode key the call fails; with it, every call warns on stderr.
    if "--auth-mode key" not in line:
        sys.exit("ERROR: Please specify --account-key, --sas-token or --connection-string")
    sys.stderr.write("WARNING: There are no credentials provided in your command and environment, "
                     "we will query for account key for your storage account.\n")
    if line.startswith("storage table list"):
        if scenario == "tablefail": sys.exit("ERROR: table endpoint refused")
        if scenario != "tablemissing": print("FeedState")
    elif "length" in line:
        if scenario == "entityfail": sys.exit("ERROR: entity query refused")
        print({"entityzero": "0", "entitynan": "None"}.get(scenario, "3"))
    else:
        print("Collection  Processed")
save()
'''


CANARY = "MKEY-CANARY-555"


def _play(scenario, script=SCRIPT, flags=()):
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
                           ("sleep", 'echo "sleep $*" >> "%s.calls"' % state)):
            path = os.path.join(bin_dir, name)
            open(path, "w").write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_STATE=state,
                   FAKE_SCENARIO=scenario, FAKE_KEY=CANARY, SUBSCRIPTION_ID="sub-1", RESOURCE_GROUP="rg",
                   WORKSPACE_NAME="" if scenario == "novars" else "ws")
        r = subprocess.run(["bash", *flags, os.path.join(tmp, "scripts", "portal_test.sh")], env=env,
                           capture_output=True, text=True, timeout=300)
        calls = open(state + ".calls").read() if os.path.exists(state + ".calls") else ""
        return r.returncode, r.stdout + r.stderr, calls
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


SCENARIOS = ["healthy", "healthy_delayed", "healthy_lower", "uuid4", "uuid4_delayed", "noaudit", "failed", "partial",
             "nosend", "nosend2", "rowsshort", "successfailed", "wrongsub", "fagone", "fashowfail", "fashowempty",
             "idfail", "idempty", "tablefail", "entityfail", "notlogged", "subfail", "falistfail", "startfail", "nofa",
             "startok", "rolefail", "norole", "keyfail", "curlfail", "salistfail", "nosa", "stopfail", "stuckrunning",
             "stopreadfail", "tirowsna", "tirows1na", "tidistna", "auditreadna", "auditcountna", "auditbeforena", "curl500", "tablemissing",
             "entityzero", "entitynan", "novars", "curl503", "curl503once", "curl403", "keyempty"]
# Scenarios are independent (own temp dir, own fake state): run them side by side.
with ThreadPoolExecutor(max_workers=4) as pool:
    RESULTS = dict(zip(SCENARIOS, pool.map(_play, SCENARIOS)))


XRUN = _play("healthy", flags=("-x",))   # bash -x: the key must not show anywhere


def play(scenario):
    rc, out, calls = RESULTS[scenario]
    CALLS[scenario] = calls
    return rc, out


CALLS = {}
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
# Cost guard (5587 TL): the cleanup trap stops the app on a healthy exit and on a mid-test exit.
check("functionapp stop" in CALLS["healthy"], "healthy: cleanup never called functionapp stop\n%s" % CALLS["healthy"])
for sc in ("fashowfail", "startfail", "fagone"):
    play(sc)
    check("functionapp stop" in CALLS[sc], "%s: a test aborted midway did not stop the app\n%s" % (sc, CALLS[sc]))
# A stop that failed, or an app still Running / unreadable afterwards, is never exit 0.
for sc, needle in (("stopfail", "functionapp stop failed"), ("stuckrunning", "not Stopped"), ("stopreadfail", "not Stopped")):
    rc, out = play(sc)
    check(rc != 0 and needle in out and "RESULT: PASS" in out,
          "%s: stop not confirmed but exit code is %s (needle %r)\n%s" % (sc, rc, needle, out[-500:]))
# Late ingestion of the second run's rows must not turn it red either.
rc, out = play("healthy_delayed")
check(rc == 0, "healthy product with late ingestion did not pass\n%s" % out[-900:])

# The reader takes both spellings of the response keys: live is Tables/Rows,
# lower case is the older shape.
rc, out = play("healthy_lower")
check(rc == 0 and "RESULT: PASS" in out, "lower-case response keys were not read (rc=%s)\n%s" % (rc, out[-900:]))

# Broken products must not pass.
rc, out = play("uuid4")
check(rc != 0 and row(out, "Same IDs not duplicated") == "FAIL (ids grew)", "ids that grow on every run read as dedup: %r" % row(out, "Same IDs not duplicated"))
rc, out = play("uuid4_delayed")
check(rc != 0 and row(out, "Same IDs not duplicated") == "FAIL (ids grew)",
      "dedup was judged before the second run's rows landed: %r" % row(out, "Same IDs not duplicated"))
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
check(rc != 0 and row(out, "Same IDs not duplicated") == "CANNOT-MEASURE",
      "a second run that sent nothing proved dedup: %r" % row(out, "Same IDs not duplicated"))

# TI rows below what run 1 says it created: judged on run 1's own count, not on
# the total after run 2 (which would cover the gap).
rc, out = play("rowsshort")
check(rc != 0 and (row(out, "TI Indicators") or "").startswith("FAIL"), "fewer rows than created passed: %r" % row(out, "TI Indicators"))
# Success next to failed indicators is not a pass.
rc, out = play("successfailed")
check(rc != 0 and (row(out, "Import Run") or "").startswith("FAIL"), "Success with failed>0 passed: %r" % row(out, "Import Run"))

# Wrong subscription: stop before any app lookup, trigger or stop (5587 TL guard).
rc, out = play("wrongsub")
check(rc != 0 and "az is on subscription 'sub-OTHER'" in out, "wrongsub: no subscription error (rc=%s)\n%s" % (rc, out[-400:]))
check("functionapp" not in CALLS["wrongsub"] and "curl" not in CALLS["wrongsub"] and "=== Pre-Test" not in out,
      "wrongsub: the script went on to act in the wrong subscription\n%s" % CALLS["wrongsub"])

# functionapp show: rc 3 is absent, any other failure or an empty answer is "could not look".
rc, out = play("fagone")
check(rc != 0 and "FAIL (absent)" in out and "CANNOT MEASURE" not in out, "fagone: absent app read wrong\n%s" % out[-400:])
for sc in ("fashowfail", "fashowempty"):
    rc, out = play(sc)
    check(rc != 0 and "az functionapp show failed" in out and "absent" not in out, "%s: failed look not CANNOT MEASURE\n%s" % (sc, out[-400:]))
# identity show: a failed or empty look is CANNOT MEASURE and the run goes on.
for sc in ("idfail", "idempty"):
    rc, out = play(sc)
    check("Sentinel Contributor: CANNOT MEASURE" in out and "=== Test 4" in out, "%s: role check skipped silently\n%s" % (sc, out[-500:]))

# A failing storage call must print CANNOT MEASURE, not end the script silently.
for sc, what in (("tablefail", "FeedState Table"), ("entityfail", "Checkpoint entries")):
    rc, out = play(sc)
    check("%s: CANNOT MEASURE" % what in out and "=== Test 4" in out,
          "%s: failing storage call did not report CANNOT MEASURE and continue (rc=%s)\n%s" % (sc, rc, out[-500:]))
    check(rc != 0 and row(out, "Storage Checkpoint") == "CANNOT-MEASURE",
          "%s: summary row is %r, not CANNOT-MEASURE" % (sc, row(out, "Storage Checkpoint")))

# An az or curl call that fails must say so; the script must not die silently
# (set -e + X=$(...)) and the summary must not read a failed look as a pass or an absence.
for sc, needle in (("notlogged", "Not logged in"), ("subfail", "az account show failed"),
                   ("falistfail", "az functionapp list failed"), ("startfail", "could not start Function App")):
    rc, out = play(sc)
    check(rc != 0 and needle in out, "%s: expected %r, got rc=%s\n%s" % (sc, needle, rc, out[-400:]))
rc, out = play("nofa")
check(rc != 0 and "No Function App found" in out and "CANNOT MEASURE" not in out, "nofa: absent app read wrong\n%s" % out[-400:])
rc, out = play("startok")
check(rc == 0, "a stopped app that starts did not pass (rc=%s)\n%s" % (rc, out[-400:]))
rc, out = play("rolefail")
check("Sentinel Contributor: CANNOT MEASURE" in out and "=== Test 4" in out, "rolefail: no CANNOT MEASURE / script stopped\n%s" % out[-500:])
rc, out = play("norole")
check("Sentinel Contributor: MISSING" in out and "may fail" not in out, "norole: MISSING branch not reached\n%s" % out[-500:])
check(rc != 0 and row(out, "Sentinel Contributor") == "FAIL (missing)", "norole: a missing role is not a FAIL row: %r (rc=%s)" % (row(out, "Sentinel Contributor"), rc))
check(row(play("healthy")[1], "Sentinel Contributor") == "PASS", "healthy: role row is not PASS")
for sc in ("rolefail", "idfail"):
    check(row(play(sc)[1], "Sentinel Contributor") == "CANNOT-MEASURE", "%s: role row is %r" % (sc, row(play(sc)[1], "Sentinel Contributor")))
rc, out = play("tirowsna")
check(rc != 0 and row(out, "Same IDs not duplicated") == "CANNOT-MEASURE" and "unreadable" in out,
      "tirowsna: an unreadable row count read as %r" % row(out, "Same IDs not duplicated"))
for sc in ("keyfail", "curlfail"):
    rc, out = play(sc)
    check(rc != 0 and "Trigger: CANNOT MEASURE" in out and out.count("Trigger: CANNOT MEASURE") == 2
          and "=== Test 4" in out and row(out, "Import Run") == "CANNOT-MEASURE" and row(out, "Second Run") == "CANNOT-MEASURE",
          "%s: failed trigger not reported as CANNOT-MEASURE twice (rc=%s)\n%s" % (sc, rc, out[-700:]))
check("Could not get master key" in play("keyfail")[1], "keyfail: reason not printed")
check("curl failed" in play("curlfail")[1], "curlfail: reason not printed")
# (1) az's stderr WARNING must not leak into the answers; a missing table is not a pass (5).
rc, out = play("healthy")
check("Checkpoint entries: 3\n" in out and "FeedState Table: OK\n" in out and "WARNING" not in out,
      "healthy: the az WARNING leaked into the storage lines\n%s" % out[-900:])
rc, out = play("tablemissing")
check(rc != 0 and row(out, "Storage Checkpoint") == "FAIL (absent)" and "FeedState Table: OK" not in out
      and "WARNING" not in out, "tablemissing: absent table read as %r\n%s" % (row(out, "Storage Checkpoint"), out[-700:]))
rc, out = play("entityzero")
check(rc != 0 and row(out, "Storage Checkpoint") == "FAIL (empty)", "entityzero: no checkpoint read as %r" % row(out, "Storage Checkpoint"))
rc, out = play("entitynan")
check(rc != 0 and row(out, "Storage Checkpoint") == "CANNOT-MEASURE", "entitynan: a non-number read as %r" % row(out, "Storage Checkpoint"))
# The failure reason survives the stderr split.
check("table endpoint refused" in play("tablefail")[1] and "entity query refused" in play("entityfail")[1],
      "tablefail/entityfail: the az error text is not shown")
# (5) every storage table/entity call carries --auth-mode key (the fake fails the call without it).
for sc in ("healthy", "tablemissing"):
    play(sc)
    bad = [l for l in CALLS[sc].splitlines() if l.startswith("az storage ") and not l.startswith("az storage account")
           and "--auth-mode key" not in l]
    check(not bad, "%s: storage call without --auth-mode key: %s" % (sc, bad))
check(row(play("healthy")[1], "Storage Checkpoint") == "PASS", "healthy: Storage Checkpoint is not PASS")

# (4) unreadable counts reach the TI row as CANNOT-MEASURE, never FAIL (NA) or PASS.
for sc in ("tirows1na", "tidistna"):
    rc, out = play(sc)
    check(rc != 0 and row(out, "TI Indicators") == "CANNOT-MEASURE" and "query refused" in out,
          "%s: TI row is %r (rc=%s)" % (sc, row(out, "TI Indicators"), rc))
rc, out = play("auditcountna")
check(rc != 0 and row(out, "Import Run") == "CANNOT-MEASURE", "auditcountna: Import Run is %r" % row(out, "Import Run"))
# Only the count taken BEFORE the trigger fails: waiting anyway would end as TIMEOUT, not CANNOT-MEASURE.
rc, out = play("auditbeforena")
check(rc != 0 and row(out, "Import Run") == "CANNOT-MEASURE" and "Waiting for the run" not in out.split("=== Test 4")[0],
      "auditbeforena: Import Run is %r" % row(out, "Import Run"))
rc, out = play("auditreadna")
check(rc != 0 and row(out, "Import Run") == "CANNOT-MEASURE", "auditreadna: Import Run is %r" % row(out, "Import Run"))
# A trigger the host answered with HTTP 500 is a failed run on both runs and does not wait for someone else's audit row.
rc, out = play("curl500")
check(rc != 0 and row(out, "Import Run") == "FAIL (HTTP 500)" and row(out, "Second Run") == "FAIL (HTTP 500)"
      and "Waiting for the run" not in out, "curl500: runs read as %r / %r" % (row(out, "Import Run"), row(out, "Second Run")))
# The master key: off argv, in a 0600 header file, not in bash -x output, never an empty one.
def curls(sc): return [l for l in CALLS[sc].splitlines() if l.startswith("curl ")]
def n15(sc): return CALLS[sc].count("sleep 15\n")
play("healthy")
check(CANARY not in CALLS["healthy"] and curls("healthy") and all("--max-time 60" in l for l in curls("healthy")),
      "healthy: the key is on the curl argv, or a curl call has no --max-time 60\n%s" % curls("healthy"))
check("hdrfile-mode 600" in CALLS["healthy"], "healthy: the key header file is not 0600")
check(XRUN[0] == 0 and CANARY not in XRUN[1] and CANARY not in XRUN[2],
      "bash -x showed the master key (rc=%s): %s" % (XRUN[0], [l for l in XRUN[1].splitlines() if CANARY in l][:3]))
rc, out = play("keyempty")
check(rc != 0 and "Could not get master key" in out and row(out, "Import Run") == "CANNOT-MEASURE" and not curls("keyempty"),
      "keyempty: an empty master key went on to curl (rc=%s)\n%s" % (rc, out[-400:]))
# Cold start: 5xx or no answer is retried (3 tries, 15 s apart); 4xx is not.
rc, out = play("curl503once")
check(rc == 0 and "RESULT: PASS" in out and len(curls("curl503once")) == 4 and n15("curl503once") == 2,
      "curl503once: 503 then 202 did not pass after a retry (rc=%s, curls=%d, sleeps15=%d)\n%s" % (rc, len(curls("curl503once")), n15("curl503once"), out[-400:]))
rc, out = play("curl503")
check(rc != 0 and row(out, "Import Run") == "FAIL (HTTP 503)" and row(out, "Second Run") == "FAIL (HTTP 503)"
      and len(curls("curl503")) == 6 and n15("curl503")  == 4,
      "curl503: always-503 is not FAIL after 3 tries per trigger (curls=%d, sleeps15=%d)" % (len(curls("curl503")), n15("curl503")))
rc, out = play("curl403")
check(rc != 0 and row(out, "Import Run") == "FAIL (HTTP 403)" and len(curls("curl403")) == 2 and n15("curl403") == 0,
      "curl403: a 4xx was retried or not FAIL (curls=%d, sleeps15=%d)" % (len(curls("curl403")), n15("curl403")))
check(len(curls("curlfail")) == 6 and n15("curlfail") == 4, "curlfail: no answer was not tried 3 times per trigger (curls=%d)" % len(curls("curlfail")))

# Missing config stops before any az call.
rc, out = play("novars")
check(rc != 0 and "ERROR: set SUBSCRIPTION_ID" in out and "az " not in CALLS["novars"], "novars: the script went on without config\n%s" % out[-300:])

rc, out = play("salistfail")
check(rc != 0 and "Storage Account: CANNOT MEASURE" in out and row(out, "Storage Checkpoint") == "CANNOT-MEASURE",
      "salistfail: a failed look read as %r\n%s" % (row(out, "Storage Checkpoint"), out[-500:]))
rc, out = play("nosa")
check(rc != 0 and row(out, "Storage Checkpoint") == "FAIL (absent)", "nosa: absent account read as %r" % row(out, "Storage Checkpoint"))

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("portal_test.sh passes a healthy product and fails broken ones: OK")
