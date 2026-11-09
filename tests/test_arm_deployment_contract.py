#!/usr/bin/env python3
"""Keep deployment cloud/location wiring and the README parameter contract explicit."""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "azuredeploy.json").read_text(encoding="utf-8"))
FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)


def no_fixed_hosts(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$schema":
                continue
            no_fixed_hosts(key, path + "/<key>")
            no_fixed_hosts(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            no_fixed_hosts(child, path + "/" + str(index))
    elif isinstance(value, str):
        for host in ("core.windows.net", "management.azure.com", "blob.core"):
            check(host not in value.lower(), "fixed Azure host outside $schema: %s at %s" % (host, path))


no_fixed_hosts(TEMPLATE)
check(TEMPLATE.get("parameters", {}).get("location", {}).get("defaultValue") == "[resourceGroup().location]",
      "location default must be resourceGroup().location")
SUPPORT_TYPES = (
    "Microsoft.Storage/storageAccounts", "Microsoft.ManagedIdentity/userAssignedIdentities",
    "Microsoft.Web/serverfarms", "Microsoft.Insights/components", "Microsoft.Web/sites",
    "Microsoft.Resources/deploymentScripts",
)
for resource_type in SUPPORT_TYPES:
    found = [r for r in TEMPLATE["resources"] if r.get("type", "").lower() == resource_type.lower()]
    check(len(found) == 1, "expected one support resource: " + resource_type)
    for resource in found:
        check(resource.get("location") == "[parameters('location')]",
              "support location must use location parameter: " + resource_type)

scripts = [r for r in TEMPLATE["resources"] if r.get("type") == "Microsoft.Resources/deploymentScripts"]
if len(scripts) == 1:
    properties = scripts[0]["properties"]
    environment = {item["name"]: item.get("value") for item in properties.get("environmentVariables", [])}
    check(environment.get("RESOURCE_MANAGER") == "[environment().resourceManager]", "RESOURCE_MANAGER must come from environment()")
    check(environment.get("STORAGE_SUFFIX") == "[environment().suffixes.storage]", "STORAGE_SUFFIX must come from environment()")
    script = properties.get("scriptContent", "")
    arm_request = 'az rest --method get --url "${RESOURCE_MANAGER%/}/subscriptions/$SUB_ID/resourceGroups/$RG_NAME/providers/Microsoft.Web/sites/$FA_NAME/functions?api-version='
    check(script.count(arm_request) == 1, "deploymentScript ARM URL must trim slash and use /subscriptions/")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
section = re.search(r"^## Parameters\s*\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
check(section is not None, "README Parameters table is missing")
documented = set(re.findall(r"^\|\s*`([^`]+)`\s*\|", section.group(1), re.M)) if section else set()
missing = set(TEMPLATE["parameters"]) - documented
check(not missing, "README parameter table missing: " + ", ".join(sorted(missing)))

if FAILURES:
    print("FAIL test_arm_deployment_contract")
    for message in FAILURES:
        print("  - " + message)
    sys.exit(1)
print("ok test_arm_deployment_contract (cloud hosts, six locations, ARM URL, %d documented parameters)" % len(TEMPLATE["parameters"]))
