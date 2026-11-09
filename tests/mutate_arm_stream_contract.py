#!/usr/bin/env python3
"""Run ARM stream-contract mutations in an isolated copy of this repository."""

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types


REPO = pathlib.Path(__file__).resolve().parents[1]
TEST = "tests/test_arm_stream_contract.py"
MUTATIONS = (
    ("Feeds log IndicatorType casing", "FunctionApp/stix_builder.py", '"IndicatorType": item.get("feed_type", "unknown")', '"Indicatortype": item.get("feed_type", "unknown")', "build_feed_log fields differ from SOCRadar_Feeds_CL"),
    ("Feeds stream setting literal mismatch", "azuredeploy.json", '"value": "[variables(\'FeedsStreamName\')]"', '"value": "Custom-Wrong_Feeds_CL"', "expected a direct ARM variable expression, got 'Custom-Wrong_Feeds_CL'"),
    ("Audit stream setting literal mismatch", "azuredeploy.json", '"value": "[variables(\'AuditStreamName\')]"', '"value": "Custom-Wrong_Audit_CL"', "expected a direct ARM variable expression, got 'Custom-Wrong_Audit_CL'"),
    ("AZURE_CLIENT_ID removed", "azuredeploy.json", '"name": "AZURE_CLIENT_ID",', '"name": "AZURE_CLIENT_ID_REMOVED",', "AZURE_CLIENT_ID app setting is missing"),
    ("Feeds posts through audit stream", "FunctionApp/dcr_logger.py", "self._ingest(self.feeds_endpoint, self.feeds_dcr_id, self.feeds_stream, batch)", "self._ingest(self.feeds_endpoint, self.feeds_dcr_id, self.audit_stream, batch)", "log_feeds POST URL used wrong route"),
)
STRUCTURED_MUTATIONS = (
    ("M1 Feeds table value wrong", "feeds_table", "FeedsTableName must equal 'SOCRadar_Feeds_CL'"),
    ("M2 Feeds stream variable wrong", "feeds_stream", "FeedsStreamName must equal 'Custom-' + FeedsTableName"),
    ("M3 Feeds transform project-away", "feeds_transform", "Feeds data flow transformKql must be identity 'source'"),
    ("M4 Feeds stream column type wrong", "feeds_column_type", "Feeds table/DCR name and type columns must be nonempty and exactly equal"),
)
PUBLIC_FILES = (
    "azuredeploy.json",
    "FunctionApp/dcr_logger.py",
    "FunctionApp/stix_builder.py",
    "tests/_harness.py",
    "tests/test_arm_stream_contract.py",
)


def expected_assertion_failure(result, diagnostic):
    """Only the intended test assertion, not an arbitrary non-zero, counts."""
    output = result.stdout + result.stderr
    return (
        result.returncode == 1
        and "FAIL test_arm_stream_contract" in result.stdout
        and diagnostic in result.stdout
        and not result.stderr.strip()
        and "Traceback" not in output
    )


def make_minimal_clone(destination):
    for relative in PUBLIC_FILES:
        source = REPO / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def feeds_resource(template, resource_type):
    matches = [resource for resource in template["resources"] if resource.get("type") == resource_type]
    if resource_type == "Microsoft.OperationalInsights/workspaces/tables":
        matches = [resource for resource in matches if "FeedsTableName" in resource.get("name", "")]
    elif resource_type == "Microsoft.Insights/dataCollectionRules":
        matches = [resource for resource in matches if "FeedsStreamName" in str(resource)]
    assert len(matches) == 1, "expected exactly one Feeds %s resource" % resource_type
    return matches[0]


def apply_structured_mutation(target, kind):
    template = json.loads(target.read_text(encoding="utf-8"))
    if kind == "feeds_table":
        template["variables"]["FeedsTableName"] = "SOCRadar_Wrong_Feeds_CL"
    elif kind == "feeds_stream":
        template["variables"]["FeedsStreamName"] = "Custom-Wrong_Feeds_CL"
    elif kind == "feeds_transform":
        dcr = feeds_resource(template, "Microsoft.Insights/dataCollectionRules")
        dcr["properties"]["dataFlows"][0]["transformKql"] = "source | project-away Source"
    elif kind == "feeds_column_type":
        dcr = feeds_resource(template, "Microsoft.Insights/dataCollectionRules")
        dcr["properties"]["streamDeclarations"]["[variables('FeedsStreamName')]"]["columns"][0]["type"] = "string"
    else:
        raise ValueError("unknown structured mutation %r" % kind)
    target.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")


def main():
    failed = []
    arbitrary_exception = types.SimpleNamespace(
        returncode=1,
        stdout="FAIL test_arm_stream_contract\n  - expected diagnostic\n",
        stderr="Traceback (most recent call last): RuntimeError: unrelated failure\n",
    )
    if expected_assertion_failure(arbitrary_exception, "expected diagnostic"):
        failed.append("harness accepts an arbitrary exception as a caught mutation")
    with tempfile.TemporaryDirectory(prefix="feeds-arm-stream-mut-") as temporary:
        clone = pathlib.Path(temporary) / "repo"
        make_minimal_clone(clone)
        template_target = clone / "azuredeploy.json"
        template_source = template_target.read_text(encoding="utf-8")
        for name, kind, diagnostic in STRUCTURED_MUTATIONS:
            try:
                apply_structured_mutation(template_target, kind)
            except Exception as error:
                failed.append("%s: could not make structured mutation: %s" % (name, error))
                template_target.write_text(template_source, encoding="utf-8")
                continue
            result = subprocess.run([sys.executable, TEST], cwd=clone, text=True, capture_output=True,
                                    env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
            template_target.write_text(template_source, encoding="utf-8")
            if expected_assertion_failure(result, diagnostic):
                print("CAUGHT " + name)
            else:
                failed.append("%s: expected exit 1 + assertion %r with clean stderr; got rc=%d stdout=%r stderr=%r" %
                              (name, diagnostic, result.returncode, result.stdout[-500:], result.stderr[-500:]))
        for name, relative, original, replacement, diagnostic in MUTATIONS:
            target = clone / relative
            source = target.read_text(encoding="utf-8")
            if source.count(original) != 1:
                failed.append("%s: expected one mutation target, found %d" % (name, source.count(original)))
                continue
            target.write_text(source.replace(original, replacement, 1), encoding="utf-8")
            result = subprocess.run([sys.executable, TEST], cwd=clone, text=True, capture_output=True,
                                    env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
            target.write_text(source, encoding="utf-8")
            if expected_assertion_failure(result, diagnostic):
                print("CAUGHT " + name)
            else:
                failed.append("%s: expected exit 1 + assertion %r with clean stderr; got rc=%d stdout=%r stderr=%r" %
                              (name, diagnostic, result.returncode, result.stdout[-500:], result.stderr[-500:]))
    if failed:
        print("FAIL mutate_arm_stream_contract")
        for failure in failed:
            print("  - " + failure)
        return 1
    total = len(STRUCTURED_MUTATIONS) + len(MUTATIONS)
    print("%d/%d stream-contract mutations caught in an isolated copy" % (total, total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
