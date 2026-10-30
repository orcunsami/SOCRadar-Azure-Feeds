"""The default CustomCollectionNames must name the default CustomCollectionIds."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    p = json.load(open(os.path.join(ROOT, "azuredeploy.json")))["parameters"]
    ids = [x.strip() for x in p["CustomCollectionIds"]["defaultValue"].split(",") if x.strip()]
    names = [x.strip() for x in p["CustomCollectionNames"]["defaultValue"].split(",")]
    src = open(os.path.join(ROOT, "FunctionApp", "feeds_processor.py")).read()
    known = dict(re.findall(r'"([0-9a-f]{32})":\s*"([^"]+)"', src))
    assert len(names) == len(ids), (len(names), len(ids))
    for cid, name in zip(ids, names):
        assert name, "empty default name for %s" % cid[:8]
        assert known.get(cid) == name, "default name for %s differs from the code table" % cid[:8]
    print("OK default names match: %d" % len(ids))


if __name__ == "__main__":
    sys.exit(main())
