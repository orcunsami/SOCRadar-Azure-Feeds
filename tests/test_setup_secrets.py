#!/usr/bin/env python3
"""scripts/portal_setup.sh against a fake `az`: secrets off argv and xtrace, subscription guard, failed looks.

A fake `az` records every call. The deployment scenario fails the deployment and
keeps what it was handed (argv, the parameters file, its mode). The others go on
past the deployment and check that a failed look is "CANNOT MEASURE" with a
non-zero exit, that a real absence says so, and that a healthy run ends 0.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "KEY-SECRET-123456"
SAS = "SAS-SECRET-789012"
URI = "https://example.invalid/pkg.zip?sig=" + SAS

FAKE = r'''
import json, os, stat, sys
args = sys.argv[1:]
line = " ".join(args)
sc = os.environ["FAKE_SCENARIO"]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(line + "\n")
if line.startswith("account show") and "user.name" in line:
    print("tester")
elif line.startswith("account show"):
    if sc == "subfail": sys.exit("ERROR: account show refused")
    print("sub-OTHER" if sc == "wrongsub" else "sub-1")
elif line.startswith("deployment group create"):
    if sc == "deployfail":
        rec = {"argv": args}
        for a in args:
            if a.startswith("@"):
                rec["file"] = a[1:]
                rec["mode"] = stat.S_IMODE(os.stat(a[1:]).st_mode)
                rec["content"] = json.load(open(a[1:]))
        json.dump(rec, open(os.environ["FAKE_REC"], "w"))
        sys.exit("ERROR: stop here")
elif line.startswith("deployment group show"):
    if sc in ("dgshowfail", "dgshowfail_falistfail", "dgshowfail_nofa"): sys.exit("ERROR: deployment show refused")
    print("socradar-feeds-test")
elif line.startswith("functionapp list"):
    if sc == "dgshowfail_falistfail": sys.exit("ERROR: functionapp list refused")
    if sc != "dgshowfail_nofa": print("socradar-feeds-test")
elif line.startswith("functionapp show"):
    if sc == "fashowfail": sys.exit("ERROR: show refused")
    if sc == "fagone": sys.exit(3)
    print("Running")
elif line.startswith("identity show"):
    if sc == "idfail": sys.exit("ERROR: identity show refused")
    if sc == "idgone": sys.exit(3)
    if sc != "idempty": print("principal")
elif line.startswith("role assignment"):
    if sc == "rolelistfail": sys.exit("ERROR: role assignment list refused")
    # Live az answers by scope: the workspace role at the workspace, the storage role at the account.
    # Without --scope, --all spans the whole subscription and finds a role granted on ANY account.
    if "storageAccounts/srfeedstest" in line:
        if sc not in ("norolestorage", "rolestorageelsewhere"): print("Storage Table Data Contributor")
    elif "workspaces/ws" in line:
        print("Microsoft Sentinel Contributor")
    elif "--all" in line and sc != "norolestorage":
        print("Storage Table Data Contributor")
elif line.startswith("storage account list"):
    if sc == "salistfail": sys.exit("ERROR: storage account list refused")
    if sc != "nosa":
        print("/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/srfeedstest"
              if "].id" in line else "srfeedstest")
elif line.startswith("storage table list"):
    if "--auth-mode key" not in line:
        sys.exit("ERROR: Please specify --account-key, --sas-token or --connection-string")
    sys.stderr.write("WARNING: There are no credentials provided in your command and environment.\n")
    if sc == "tablefail": sys.exit("ERROR: table endpoint refused")
    if sc != "tablemissing": print("FeedState")
'''


ENVCANARY = "ENVCANARY-9876"


def play(scenario, shell_flags=(), envfile=None):
    tmp = tempfile.mkdtemp(prefix="setup-secrets-")
    try:
        os.makedirs(os.path.join(tmp, "bin"))
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(os.path.join(REPO, "scripts", "portal_setup.sh"), os.path.join(tmp, "scripts"))
        open(os.path.join(tmp, "azuredeploy.json"), "w").write("{}")
        if envfile is not None:
            open(os.path.join(tmp, "scripts", ".env"), "w").write(envfile)
        open(os.path.join(tmp, "fake.py"), "w").write(FAKE)
        for name, body in (("az", 'exec python3 "%s" "$@"' % os.path.join(tmp, "fake.py")), ("sleep", "exit 0")):
            path = os.path.join(tmp, "bin", name)
            open(path, "w").write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        rec, calls = os.path.join(tmp, "rec.json"), os.path.join(tmp, "calls.txt")
        env = dict(os.environ, PATH=os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"], FAKE_REC=rec,
                   FAKE_CALLS=calls, FAKE_SCENARIO=scenario, SUBSCRIPTION_ID="sub-1", RESOURCE_GROUP="rg",
                   WORKSPACE_NAME="ws", SOCRADAR_API_KEY=KEY, PACKAGE_URI=URI)
        r = subprocess.run(["bash", *shell_flags, os.path.join(tmp, "scripts", "portal_setup.sh")], env=env,
                           capture_output=True, text=True, timeout=60)
        return (r.returncode, r.stdout + r.stderr, open(calls).read() if os.path.exists(calls) else "",
                json.load(open(rec)) if os.path.exists(rec) else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


# Secrets: off argv, in a 0600 file that is gone after exit, never printed.
rc, out, calls, got = play("deployfail")
if got is None:
    failures.append("the deployment call never happened (rc=%s)\n%s" % (rc, out[-400:]))
else:
    if KEY in " ".join(got["argv"]) or SAS in " ".join(got["argv"]):
        failures.append("API key or SAS is on the az command line")
    if "file" not in got:
        failures.append("no parameters file was passed to az")
    else:
        if got["mode"] != 0o600:
            failures.append("parameters file mode is %o, not 600" % got["mode"])
        p = got["content"]["parameters"]
        if p.get("SocradarApiKey", {}).get("value") != KEY or p.get("PackageUri", {}).get("value") != URI:
            failures.append("the file does not carry the key and the PackageUri")
        if os.path.exists(got["file"]):
            failures.append("parameters file was left behind after the script exited")
    if KEY in out or SAS in out:
        failures.append("a secret was printed")

# bash -x must not show the key or the SAS on stderr, in the output or in any az call.
rc, out, calls, got = play("deployfail", ("-x",))
check(KEY not in out and SAS not in out and KEY not in calls and SAS not in calls,
      "bash -x printed the API key or the SAS:\n%s" % "\n".join(l for l in out.splitlines() if KEY in l or SAS in l)[:400])

# Wrong subscription: stop before any deployment (5587 TL guard).
rc, out, calls, _ = play("wrongsub")
check(rc != 0 and "az is on subscription 'sub-OTHER'" in out, "wrongsub: no subscription error (rc=%s)\n%s" % (rc, out[-300:]))
check("deployment" not in calls, "wrongsub: a deployment was attempted\n%s" % calls)
rc, out, calls, _ = play("subfail")
check(rc != 0 and "CANNOT MEASURE: az account show failed" in out and "deployment" not in calls and "az is on subscription" not in out,
      "subfail: an unreadable subscription is not CANNOT MEASURE (rc=%s)\n%s" % (rc, out[-300:]))

# Failed looks after the deployment are CANNOT MEASURE and never "Setup Complete"; absence says so.
for sc, needle, other in (("fashowfail", "CANNOT MEASURE: az functionapp show failed", "absent"),
                          ("fagone", "FAIL (absent): Function App", "CANNOT MEASURE"),
                          ("idfail", "CANNOT MEASURE: az identity show failed", "absent"),
                          ("idempty", "CANNOT MEASURE: az identity show failed", "absent"),
                          ("idgone", "FAIL (absent): managed identity", "CANNOT MEASURE"),
                          ("tablefail", "FeedState Table: CANNOT MEASURE", "MISSING"),
                          ("tablemissing", "FeedState Table: MISSING", "CANNOT MEASURE")):
    rc, out, calls, _ = play(sc)
    check(rc != 0 and needle in out and other not in out and "Setup Complete" not in out,
          "%s: expected %r only, rc=%s\n%s" % (sc, needle, rc, out[-400:]))

# Storage account: a failed look is CANNOT MEASURE, an absent account is FAIL; neither is "Setup Complete" (2).
for sc, needle, other in (("salistfail", "CANNOT MEASURE: az storage account list failed", "absent"),
                          ("nosa", "FAIL (absent): no storage account", "CANNOT MEASURE"),
                          ("rolelistfail", "CANNOT MEASURE: az role assignment list failed", "MISSING")):
    rc, out, calls, _ = play(sc)
    check(rc != 0 and needle in out and other not in out and "Setup Complete" not in out and "WARNING: No storage" not in out,
          "%s: expected %r only, rc=%s\n%s" % (sc, needle, rc, out[-400:]))

# The storage role is read on the storage account; a role held on some other account does not count (3).
rc, out, calls, _ = play("norolestorage")
check(rc != 0 and "Storage Table Data Contributor: MISSING" in out and "Setup Complete" not in out,
      "norolestorage: a missing storage role went on, rc=%s\n%s" % (rc, out[-300:]))
rc, out, calls, _ = play("rolestorageelsewhere")
check(rc != 0 and "Storage Table Data Contributor: MISSING" in out and "Setup Complete" not in out,
      "rolestorageelsewhere: a role held elsewhere counted, rc=%s\n%s" % (rc, out[-300:]))
rc, out, calls, _ = play("healthy")
check(all(" --all" not in l for l in calls.splitlines() if l.startswith("role assignment")),
      "a role lookup spans the whole subscription (--all):\n%s" % calls)
check(any("--scope /subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/srfeedstest" in l
          for l in calls.splitlines() if l.startswith("role assignment")), "the storage role is not looked up by storage scope")
# (5) table calls carry --auth-mode key: the fake fails the call without it.
check(all("--auth-mode key" in l for l in calls.splitlines() if l.startswith("storage table")), "a table call has no --auth-mode key")

# Deployment outputs unreadable: look in the resource group, say so; both unreadable is CANNOT MEASURE, none is absent.
rc, out, calls, _ = play("dgshowfail")
check(rc == 0 and "unreadable" in out and "Function App: socradar-feeds-test" in out, "dgshowfail: no fallback, rc=%s\n%s" % (rc, out[-400:]))
rc, out, calls, _ = play("dgshowfail_falistfail")
check(rc != 0 and "CANNOT MEASURE: az functionapp list failed" in out and "Setup Complete" not in out and "absent" not in out,
      "dgshowfail_falistfail: rc=%s\n%s" % (rc, out[-400:]))
rc, out, calls, _ = play("dgshowfail_nofa")
check(rc != 0 and "FAIL (absent): no Function App" in out and "CANNOT MEASURE" not in out and "Setup Complete" not in out,
      "dgshowfail_nofa: rc=%s\n%s" % (rc, out[-400:]))

# A .env that does not load says so (line numbers only: bash quotes the key) and stops before any deployment.
for label, body in (("syntax error", "GOOD=1\nSOCRADAR_FEED=%s (oops\n" % ENVCANARY),
                    ("failing last command", "GOOD=1\nfalse %s\n" % ENVCANARY)):
    rc, out, calls, _ = play("healthy", envfile=body)
    check(rc != 0 and ".env did not load" in out and ENVCANARY not in out and "deployment" not in calls
          and "Setup Complete" not in out,
          ".env %s: silent, leaked the value or went on (rc=%s)\n%s" % (label, rc, out[-300:]))
check("line 2" in play("healthy", envfile="GOOD=1\nSOCRADAR_FEED=%s (oops\n" % ENVCANARY)[1], ".env error does not name the line")
rc, out, calls, _ = play("healthy", envfile="GOOD=1\n")
check(rc == 0 and "Setup Complete" in out, "a good .env did not pass (rc=%s)\n%s" % (rc, out[-300:]))

# A healthy run does reach the end, so the checks above can tell pass from fail.
rc, out, calls, _ = play("healthy")
check(rc == 0 and "Setup Complete" in out and "(Running)" in out, "healthy: rc=%s\n%s" % (rc, out[-400:]))

if failures:
    for f in failures:
        print("FAIL " + f)
    sys.exit(1)
print("portal_setup.sh: secrets off argv and xtrace, subscription guard, failed looks: OK")
