#!/usr/bin/env python3
"""scripts/test_deploy_paths.sh creates and deletes resource groups, so it runs here against a fake `az`.

No live deployment: `az`, `curl`, `sleep` and the python3 sleeps are shims in a
temp dir. Checked: the wrong subscription creates and deploys nothing, an
unreadable subscription says why, the API key stays off argv and off the output,
a healthy fake passes all four paths, and every failed look (a read, a restart,
a workspace create, a group delete) comes out CANNOT or non-zero, never PASS.
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

FAKE = r'''
import json, os, stat, sys
args = sys.argv[1:]
line = " ".join(args)
sc = os.environ["FAKE_SCENARIO"]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(line + "\n")
name = args[args.index("-n") + 1] if "-n" in args else ""
if line.startswith("account show"):
    if sc == "subfail": sys.exit("ERROR: account show refused")
    print("sub-OTHER" if sc == "wrongsub" else "sub-1")
elif line.startswith("group create"):
    if sc == "groupcreatefail": sys.exit("ERROR: group create refused")
    open(os.environ["FAKE_CALLS"] + ".groups", "a").write(name + "\n")      # only a created group exists
elif line.startswith("group exists"):
    if sc == "existsfail": sys.exit("ERROR: group exists refused")
    gf = os.environ["FAKE_CALLS"] + ".groups"
    print("true" if os.path.exists(gf) and name in open(gf).read().split() else "false")
elif line.startswith("group delete"):
    if sc == "deletefail": sys.exit("ERROR: group delete refused")
    gf = os.environ["FAKE_CALLS"] + ".groups"
    if not (os.path.exists(gf) and name in open(gf).read().split()):
        sys.exit("ERROR: (ResourceGroupNotFound) Resource group '%s' could not be found." % name)
elif line.startswith("deployment group create"):
    rec = {"argv": args}
    for a in args:
        if a.startswith("@"):
            rec["file"] = a[1:]
            rec["mode"] = stat.S_IMODE(os.stat(a[1:]).st_mode)
            rec["content"] = json.load(open(a[1:]))
    json.dump(rec, open(os.environ["FAKE_REC"], "w"))
    if sc == "keycheck" or name == "path-a":
        sys.exit("ERROR: refused, SocradarApiKey=" + os.environ["FAKE_KEY"])
elif line.startswith("deployment group show"):
    if sc == "showfail": sys.exit("ERROR: deployment show refused")
    if "workspaceId" in line:
        print("" if sc == "wsidbad" else "12345678-aaaa-bbbb-cccc-1234567890ab")
    else:
        print("Failed" if name == "path-a" else "Succeeded")
elif line.startswith("deployment operation group list"):
    if sc == "opsfail": sys.exit("ERROR: operation list refused")
    if "length(@)" in line: print("0")                      # B: the precheck did not run
    elif "provisioningState=='Failed'" in line:
        if name == "path-a": print("precheck-workspace-exists")
    else: print("Succeeded")                                 # C: the precheck's own state
elif line.startswith("resource list"):
    print("0")
elif line.startswith("functionapp list"):
    if sc == "falistfail": sys.exit("ERROR: functionapp list refused")
    print("socradar-feeds-app")
elif line.startswith("rest "):
    if sc == "restfail": sys.exit("ERROR: functions list refused")
    print("0" if sc == "zerofunc" else "1")
elif line.startswith("functionapp config appsettings list"):
    if sc == "pointerfail": sys.exit("ERROR: appsettings refused")
    print("1" if sc == "pointerone" else "https://x.blob.core.windows.net/function-releases/p.zip?sig=S")
elif line.startswith("functionapp restart"):
    if sc == "restartfail": sys.exit("ERROR: restart refused")
elif line.startswith("monitor log-analytics"):
    if sc == "wscreatefail": sys.exit("ERROR: workspace create refused")
'''


def row(out, name):
    """Status of one result line: the name, then PASS, FAIL or CANNOT."""
    for l in out.splitlines():
        if l.startswith(name + " "):
            return l[len(name):].split()[0]
    return None


def play(scenario, flags=()):
    tmp = tempfile.mkdtemp(prefix="deploy-paths-")
    try:
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        os.makedirs(os.path.join(tmp, "scripts"))
        shutil.copy(os.path.join(REPO, "scripts", "test_deploy_paths.sh"), os.path.join(tmp, "scripts"))
        open(os.path.join(tmp, "azuredeploy.json"), "w").write("{}")
        open(os.path.join(tmp, "fake.py"), "w").write(FAKE)
        real_py = sys.executable
        shims = {"az": 'exec "%s" "%s" "$@"' % (real_py, os.path.join(tmp, "fake.py")),
                 "curl": "echo 200", "sleep": "exit 0",
                 # the script sleeps through python3 -c; everything else runs for real
                 "python3": 'case "$*" in *time.sleep*) exit 0;; esac\nexec "%s" "$@"' % real_py}
        for name, body in shims.items():
            path = os.path.join(bin_dir, name)
            open(path, "w").write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        rec, calls = os.path.join(tmp, "rec.json"), os.path.join(tmp, "calls.txt")
        env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_REC=rec, FAKE_CALLS=calls,
                   FAKE_SCENARIO=scenario, FAKE_KEY=KEY, TEST_SUBSCRIPTION_ID="sub-1", TEST_SOCRADAR_API_KEY=KEY)
        # Never let a real az run: the shim must be what the script resolves.
        who = subprocess.run(["bash", "-c", "command -v az"], env=env, capture_output=True, text=True).stdout.strip()
        if who != os.path.join(bin_dir, "az"):
            raise SystemExit("ABORT: az resolves to %r, not the fake" % who)
        r = subprocess.run(["bash", *flags, os.path.join(tmp, "scripts", "test_deploy_paths.sh")], env=env,
                           capture_output=True, text=True, timeout=100)
        return (r.returncode, r.stdout + r.stderr, open(calls).read() if os.path.exists(calls) else "",
                json.load(open(rec)) if os.path.exists(rec) else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


rc, out, calls, _ = play("wrongsub")
check(rc != 0 and "az is on 'sub-OTHER'" in out, "wrongsub: no refusal (rc=%s)\n%s" % (rc, out[-300:]))
check("group" not in calls and "deployment" not in calls, "wrongsub: the script created, deployed or deleted\n%s" % calls)

rc, out, calls, _ = play("subfail")
check(rc != 0 and "account show refused" in out and "CANNOT MEASURE" in out and "group" not in calls,
      "subfail: reason not shown or script went on (rc=%s)\n%s" % (rc, out[-300:]))

rc, out, calls, got = play("keycheck")
if got is None:
    failures.append("keycheck: the deployment call never happened (rc=%s)\n%s" % (rc, out[-400:]))
else:
    check(KEY not in " ".join(got["argv"]) and KEY not in calls, "the API key is on the az command line")
    check(got.get("mode") == 0o600 and got["content"]["parameters"]["SocradarApiKey"]["value"] == KEY,
          "the key file is not 0600 or does not carry the key (mode %s)" % oct(got.get("mode", 0)))
    check(not os.path.exists(got.get("file", "/nonexistent")), "the key file was left behind")
check(KEY not in out, "the API key reached the output (an az error echoed it)")
def groups(calls, verb):
    return sorted(l.split()[3] for l in calls.splitlines() if l.startswith("group %s -n " % verb))


def deleted_all(sc, calls):
    """Every group the script created gets its own delete call (one missing = billing)."""
    made, gone = groups(calls, "create"), groups(calls, "delete")
    check(len(made) == 2 and made == gone, "%s: created %s but deleted %s" % (sc, made, gone))


check("group delete" in calls, "keycheck: the groups the script created were not queued for deletion")
deleted_all("keycheck", calls)

# bash -x must not show the API key (the assignments, the SK= prefix).
rc, out, calls, _ = play("healthy", ("-x",))
check(rc == 0 and KEY not in out and KEY not in calls,
      "bash -x printed the API key (rc=%s): %s" % (rc, [l for l in out.splitlines() if KEY in l][:3]))

ALL = ["A deployment fails", "A leaves nothing behind", "A blames the precheck", "B greenfield succeeds",
       "B skips the precheck", "B indexes the function", "B stores the package where the app reloads it",
       "B still serves after a restart", "C precheck succeeds", "C precheck resolves customerId",
       "C deployment succeeds", "D redeploy succeeds", "D still has the package after redeploy",
       "D still stores the package where the app reloads it"]
rc, out, calls, _ = play("healthy")
deleted_all("healthy", calls)
check(rc == 0 and all(row(out, n) == "PASS" for n in ALL) and "All four deployment paths" in out,
      "healthy fake did not pass every path (rc=%s): %s\n%s" % (rc, [(n, row(out, n)) for n in ALL if row(out, n) != "PASS"], out[-600:]))

# A failed look is CANNOT, never PASS and never FAIL; the script still exits non-zero.
def cannot(sc, names):
    rc, out, calls, _ = play(sc)
    bad = [(n, row(out, n)) for n in names if row(out, n) != "CANNOT"]
    check(rc != 0 and not bad, "%s: expected CANNOT for %s, got %s (rc=%s)\n%s" % (sc, names, bad, rc, out[-500:]))
    return out

cannot("restartfail", ["B still serves after a restart"])
cannot("showfail", ["A deployment fails", "B greenfield succeeds", "C deployment succeeds", "D redeploy succeeds"])
cannot("opsfail", ["A blames the precheck", "B skips the precheck", "C precheck succeeds"])
cannot("restfail", ["B indexes the function", "D still has the package after redeploy"])
cannot("falistfail", ["B indexes the function", "B stores the package where the app reloads it", "B still serves after a restart"])
cannot("pointerfail", ["B stores the package where the app reloads it", "D still stores the package where the app reloads it"])
out = cannot("wscreatefail", ["C workspace for the test"])
check(row(out, "C precheck succeeds") is None, "wscreatefail: path C went on without a workspace")
# A real answer that is wrong is still FAIL.
for sc, n in (("zerofunc", "B indexes the function"), ("pointerone", "B stores the package where the app reloads it"),
              ("wsidbad", "C precheck resolves customerId")):
    rc, out, calls, _ = play(sc)
    check(rc != 0 and row(out, n) == "FAIL", "%s: %s is %r, not FAIL" % (sc, n, row(out, n)))
# Resource groups that could not be created or deleted.
rc, out, calls, _ = play("groupcreatefail")
check(rc != 0 and "could not create" in out and "deployment" not in calls, "groupcreatefail: the script went on (rc=%s)\n%s" % (rc, out[-300:]))
# An early exit never created these groups: no delete call, no false "NOT queued, may still be billing".
check("NOT queued" not in out and "group delete" not in calls and "group exists" in calls,
      "groupcreatefail: a group that never existed was deleted or alarmed (rc=%s)\n%s" % (rc, out[-300:]))
# A group that cannot be looked up is still deleted, not skipped.
rc, out, calls, _ = play("existsfail")
deleted_all("existsfail", calls)
check("NOT queued" not in out, "existsfail: a failed exists look read as a failed delete\n%s" % out[-300:])
rc, out, calls, _ = play("deletefail")
check(rc != 0 and "NOT queued" in out and "cleanup: delete queued" not in out and "All four deployment paths" in out,
      "deletefail: a delete that failed still read as queued, rc=%s\n%s" % (rc, out[-400:]))

if failures:
    for f in failures:
        print("FAIL " + f)
    sys.exit(1)
print("test_deploy_paths.sh guards (subscription, key off argv): OK")
