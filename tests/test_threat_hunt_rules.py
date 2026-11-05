#!/usr/bin/env python3
"""The threat hunting rules in "Analytic Rules/" and in azuredeploy.json say the same thing.

Five rules match SOCRadar Threat Feeds indicators against ASIM events. The yaml files are
the readable copy, the template is what gets deployed. A rule that only exists in one place,
or whose query drifted between the two, would ship quietly wrong. The query text is also
checked for the invariants that keep the rules honest: only this product's indicators, only
live ones, the right ASIM parser, and every column the entities and alert text rely on.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RULES_DIR = os.path.join(REPO, "Analytic Rules")

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


# key: (parser, ObservableKey condition, entity types, algorithm-bearing)
EXPECTED = {
    "IP": ("_Im_NetworkSession", 'ObservableKey == "ipv4-addr:value"', ["IP", "Host"]),
    "Domain": ("_Im_Dns", 'ObservableKey == "domain-name:value"', ["DNS", "Host"]),
    "Url": ("_Im_WebSession", 'ObservableKey == "url:value"', ["URL", "Host"]),
    "Hash": ("_Im_FileEvent", 'ObservableKey startswith "file:hashes"', ["FileHash", "Host"]),
    "Process": ("_Im_ProcessCreate", 'ObservableKey startswith "file:hashes"', ["FileHash", "Host"]),
}


def parse_yaml(text):
    out = {}
    out["query"] = text.split("query: |\n", 1)[1].split("\nentityMappings:", 1)[0]
    out["query"] = "\n".join(l[2:] if l.startswith("  ") else l for l in out["query"].split("\n"))
    head = text.split("query: |\n", 1)[0]
    for key in ("id", "name", "severity", "queryFrequency", "queryPeriod", "kind", "version"):
        m = re.search(r"^%s: (.+)$" % key, text, re.M)
        out[key] = m.group(1).strip() if m else None
    out["tactics"] = re.findall(r"^  - (\w+)$", text.split("tactics:\n", 1)[1].split("relevantTechniques:", 1)[0], re.M)
    out["techniques"] = re.findall(r"^  - (T\d+)$", text.split("relevantTechniques:\n", 1)[1].split("customDetails:", 1)[0], re.M)
    cd = text.split("customDetails:\n", 1)[1].split("incidentConfiguration:", 1)[0]
    out["customDetails"] = dict(re.findall(r"^  (\w+): (\w+)$", cd, re.M))
    out["display"] = re.search(r"alertDisplayNameFormat: '(.*)'", head).group(1)
    out["descfmt"] = re.search(r"alertDescriptionFormat: '(.*)'", head).group(1).replace("\\n", "\n")
    ent = text.split("entityMappings:\n", 1)[1]
    out["entities"] = [(m.group(1), re.findall(r"identifier: (\w+)\n\s+columnName: (\w+)", m.group(2)))
                       for m in re.finditer(r"- entityType: (\w+)\n    fieldMappings:\n((?:      .*\n?)+)", ent)]
    out["aggregation"] = re.search(r"aggregationKind: (\w+)", head).group(1)
    return out


def projected(query):
    """Column names of the last `| project` line."""
    line = [l for l in query.split("\n") if l.startswith("| project ")][-1][len("| project "):]
    cols, depth, cur = [], 0, ""
    for ch in line:
        if ch in "([":
            depth += 1
        if ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(cur.strip()); cur = ""
        else:
            cur += ch
    cols.append(cur.strip())
    return [c.split("=")[0].strip() if "=" in c else c for c in cols]


with open(os.path.join(REPO, "azuredeploy.json"), encoding="utf-8") as f:
    template = json.load(f)

arm = {}
for r in template["resources"]:
    if r["type"].endswith("/alertRules") and "socradar-feeds-threat-hunt" in r["name"]:
        key = re.search(r"'socradar-feeds-threat-hunt', '(\w+)'\)", r["name"]).group(1)
        arm[key] = r

check(sorted(arm) == sorted(EXPECTED), "template carries rules %s, expected %s" % (sorted(arm), sorted(EXPECTED)))
files = sorted(f for f in os.listdir(RULES_DIR) if f.startswith("SOCRadarFeedsThreatHunt")) if os.path.isdir(RULES_DIR) else []
check(len(files) == len(EXPECTED), "yaml files: %s" % files)

ids = set()
for key, (parser, keycond, entity_types) in EXPECTED.items():
    path = os.path.join(RULES_DIR, "SOCRadarFeedsThreatHunt%s.yaml" % key)
    if not os.path.isfile(path) or key not in arm:
        failures.append("%s: yaml or template rule missing" % key)
        continue
    y = parse_yaml(open(path, encoding="utf-8").read())
    r = arm[key]
    p = r["properties"]
    q = y["query"].rstrip("\n")

    check(q == p["query"].rstrip("\n"), "%s: yaml query and template query differ" % key)
    check(re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", y["id"] or ""), "%s: id is not a guid" % key)
    check(y["id"] not in ids, "%s: duplicate id" % key)
    ids.add(y["id"])
    check(y["kind"] == "Scheduled" and r["kind"] == "Scheduled", "%s: kind" % key)

    # the query only trusts this product's live indicators
    check('| where SourceSystem == "SOCRadar Threat Feeds"' in q, "%s: no SourceSystem filter" % key)
    check(keycond in q, "%s: ObservableKey condition %s missing" % (key, keycond))
    check("| where IsActive == true and ValidUntil > now()" in q, "%s: expired or inactive indicators allowed" % key)
    check("summarize LatestIndicatorTime = arg_max(TimeGenerated, *) by Id" in q, "%s: latest version per indicator not taken" % key)
    check("let ioc_lookBack = 14d;" in q and "TimeGenerated >= ago(ioc_lookBack)" in q, "%s: indicator look back" % key)
    check("let event_lookBack = 1h;" in q and "%s(starttime = ago(event_lookBack))" % parser in q,
          "%s: ASIM parser %s with event window missing" % (key, parser))
    check("| join kind=inner indicators on" in q, "%s: no join on indicators" % key)
    check(not re.search(r"\b(first|last)\s*=", q), "%s: first/last used as an alias (SYN0002)" % key)
    check("imFileEvent" not in q.replace("_Im_FileEvent", ""), "%s: workspace-deployed parser name used" % key)
    if key in ("Hash", "Process"):
        for algo in ("SHA256", "SHA1", "MD5"):
            check('"Algorithm", "%s"' % algo in q, "%s: algorithm %s not matched" % (key, algo))

    # entities, custom details and alert text only use columns the query returns
    cols = projected(q)
    check([e[0] for e in y["entities"]] == entity_types, "%s: entity types %s" % (key, [e[0] for e in y["entities"]]))
    for etype, maps in y["entities"]:
        for ident, col in maps:
            check(col in cols, "%s: entity %s.%s maps column %s the query does not return" % (key, etype, ident, col))
    for k, col in y["customDetails"].items():
        check(col in cols, "%s: custom detail %s uses column %s the query does not return" % (key, k, col))
    check(len(y["customDetails"]) >= 5, "%s: custom details thinned out" % key)
    for ph in re.findall(r"\{\{(\w+)\}\}", y["display"] + y["descfmt"]):
        check(ph in cols, "%s: alert text uses {{%s}} the query does not return" % (key, ph))

    # yaml and template agree on the settings
    check(y["queryFrequency"] == "15m" and p["queryFrequency"] == "PT15M", "%s: frequency" % key)
    check(y["queryPeriod"] == "14d" and p["queryPeriod"] == "P14D", "%s: period must be the 14d maximum" % key)
    check(y["tactics"] == p["tactics"] and y["techniques"] == p["techniques"], "%s: tactics or techniques differ" % key)
    check(y["tactics"] and y["techniques"], "%s: no tactics" % key)
    check(y["display"] == p["alertDetailsOverride"]["alertDisplayNameFormat"], "%s: display name format differs" % key)
    check(y["descfmt"] == p["alertDetailsOverride"]["alertDescriptionFormat"], "%s: description format differs" % key)
    check(y["customDetails"] == p["customDetails"], "%s: custom details differ" % key)
    check(y["name"] == p["displayName"], "%s: display name differs" % key)
    check(y["aggregation"] == "AlertPerResult" and p["eventGroupingSettings"]["aggregationKind"] == "AlertPerResult", "%s: not one alert per result" % key)
    arm_entities = [(e["entityType"], [(m["identifier"], m["columnName"]) for m in e["fieldMappings"]]) for e in p["entityMappings"]]
    check(arm_entities == y["entities"], "%s: entity mappings differ" % key)
    check(p["incidentConfiguration"]["createIncident"] is True and p["enabled"] is True, "%s: no incident or disabled" % key)
    check(p["severity"] == "[parameters('ThreatHuntRuleSeverity')]", "%s: severity not the parameter" % key)
    check(r.get("condition") == "[parameters('EnableThreatHuntRules')]", "%s: not switchable" % key)
    check(r["apiVersion"] == "2024-03-01", "%s: api version" % key)
    check(any("onboardingStates" in d for d in r["dependsOn"]), "%s: does not wait for Sentinel onboarding" % key)
    check(any("precheck-workspace-exists" in d for d in r["dependsOn"]), "%s: does not wait for the workspace precheck" % key)

params = template["parameters"]
check(params.get("EnableThreatHuntRules", {}).get("defaultValue") is True, "EnableThreatHuntRules default is not true")
check(params.get("ThreatHuntRuleSeverity", {}).get("defaultValue") == "Medium", "ThreatHuntRuleSeverity default is not Medium")
check(params.get("ThreatHuntRuleSeverity", {}).get("allowedValues") == ["Informational", "Low", "Medium", "High"],
      "ThreatHuntRuleSeverity allowed values")

if failures:
    print("FAIL test_threat_hunt_rules")
    for m in failures:
        print("  -", m)
    sys.exit(1)
print("ok test_threat_hunt_rules (%d rules)" % len(EXPECTED))
