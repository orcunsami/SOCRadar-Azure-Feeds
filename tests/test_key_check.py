#!/usr/bin/env python3
"""A rejected SOCRadar API key fails the deployment instead of a green deploy that imports nothing.

The deploymentScript asks the feed API for the first collection with the key. Only a
401 stops the deployment; a timeout or a 5xx continues (the key is not known to be bad).
The key reaches curl on stdin, never on argv, and arrives as a secure environment value.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "azuredeploy.json")
KEY = "KEY-SECRET-0123456789"

failures = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        failures.append(message)


tmpl = json.load(open(TEMPLATE))
script_res = [r for r in tmpl["resources"] if r.get("name") == "triggerFirstRun"][0]["properties"]
script = script_res["scriptContent"]
env_vars = {e["name"]: e for e in script_res["environmentVariables"]}

# wiring
check("SOCRADAR_API_KEY" in env_vars and "secureValue" in env_vars["SOCRADAR_API_KEY"]
      and "value" not in env_vars["SOCRADAR_API_KEY"], "the key must be a secureValue environment variable")
check("SocradarApiKey" in env_vars.get("SOCRADAR_API_KEY", {}).get("secureValue", ""), "the key env var is not wired to SocradarApiKey")
check("COLLECTION_ID" in env_vars and "CustomCollectionIds" in env_vars["COLLECTION_ID"].get("value", ""),
      "COLLECTION_ID is not taken from CustomCollectionIds")
check("set -x" not in script, "xtrace would print the key")
check("# key check" in script and script.index("# key check") < script.index("PACKAGE_URL"),
      "the key check must run before the package is downloaded")

block = script[script.index("# key check"):script.index("# end key check")]

SHIM = '''#!/bin/bash
echo "$@" >> "$FAKE_ARGV"
cat > "$FAKE_STDIN"
[ "$FAKE_CODE" = 000 ] && { printf 000; exit 7; }
printf %s "$FAKE_CODE"
'''


def run(code, key=KEY, collection="0cb06558728b4dc296019c93b78360d1"):
    with tempfile.TemporaryDirectory() as tmp:
        shim = os.path.join(tmp, "curl")
        open(shim, "w").write(SHIM)
        os.chmod(shim, 0o755)
        env = dict(os.environ, PATH=tmp + os.pathsep + os.environ["PATH"], FAKE_CODE=code,
                   FAKE_ARGV=os.path.join(tmp, "argv"), FAKE_STDIN=os.path.join(tmp, "stdin"),
                   SOCRADAR_API_KEY=key, COLLECTION_ID=collection)
        r = subprocess.run(["bash", "-c", "set -e\n" + block + "\necho REACHED_NEXT_STEP"], env=env,
                           capture_output=True, text=True)
        argv = open(env["FAKE_ARGV"]).read() if os.path.exists(env["FAKE_ARGV"]) else None
        stdin = open(env["FAKE_STDIN"]).read() if os.path.exists(env["FAKE_STDIN"]) else None
        return r, argv, stdin


r, argv, stdin = run("200")
check(r.returncode == 0 and "REACHED_NEXT_STEP" in r.stdout, "a good key must continue: %r" % r.stdout)
check(argv is not None and KEY not in argv, "the key is on curl's command line")
check(stdin is not None and KEY in stdin, "the key did not reach curl on stdin")
check(argv and "0cb06558728b4dc296019c93b78360d1" in argv and "platform.socradar.com" in argv, "wrong URL: %r" % argv)

r, _, _ = run("401")
check(r.returncode != 0 and "REACHED_NEXT_STEP" not in r.stdout, "a 401 must stop the deployment")
check("rejected" in r.stderr and KEY not in r.stdout + r.stderr,
      "the 401 message must be on stderr (the portal shows only stderr as the error) and not leak the key: %r / %r" % (r.stdout, r.stderr))

for code in ("500", "403", "000"):
    r, _, _ = run(code)
    check(r.returncode == 0 and "REACHED_NEXT_STEP" in r.stdout, "HTTP %s must not stop the deployment: %r" % (code, r.stdout))

for kw in (dict(key=""), dict(collection="")):
    r, argv, _ = run("401", **kw)
    check(r.returncode == 0 and argv is None, "nothing to check, curl must not be called: %r" % kw)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("key check in the deployment script: OK (%d checks)" % checks)
