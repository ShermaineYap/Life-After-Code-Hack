"""Derive the Hands-off flow from the Supervised flow so the two never drift.

The Hands-off flow is the Supervised flow minus the human approval: the
gatekeeper hands the release straight to the deployer, and the production
deploy job runs by itself when the CI variable DOWNSTREAM_AUTONOMY=hands-off.

Usage:
    python scripts/build_flows.py          # writes .gitlab/duo/flows/downstream-hands-off.yaml
    python scripts/build_flows.py --check  # exit 1 if the file is out of date (used in CI tests)
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".gitlab/duo/flows/downstream.yaml"
DST = ROOT / ".gitlab/duo/flows/downstream-hands-off.yaml"

HEADER = """# Downstream (Hands-off): GENERATED from downstream.yaml by scripts/build_flows.py. Do not edit.
#
# Same flow as the Supervised version without the human approval step:
#   triage -> developer -> lineage_reviewer -> gatekeeper -> deployer -> end
# Pair it with the CI variable DOWNSTREAM_AUTONOMY=hands-off so deploy-production runs by itself.
# You open an issue; the next time you look, the change is live (or rolled back with an incident).
"""


def derive(flow: dict) -> dict:
    flow = copy.deepcopy(flow)
    drop = {"production_approval", "decline_reporter"}
    flow["components"] = [c for c in flow["components"] if c["name"] not in drop]
    for c in flow["components"]:
        if c["name"] == "deployer":
            c["inputs"] = [i for i in c["inputs"] if not (isinstance(i, dict) and i.get("as") == "approval")]
    # Keep gatekeeper's router (it pointed at the approval step); it is re-pointed below.
    flow["routers"] = [r for r in flow["routers"] if r["from"] not in drop]
    for r in flow["routers"]:
        if r["from"] == "gatekeeper":
            r["to"] = "deployer"
    flow["prompts"] = [p for p in flow["prompts"] if p["prompt_id"] != "downstream_decline_reporter"]
    for p in flow["prompts"]:
        if p["prompt_id"] == "downstream_deployer":
            p["prompt_template"]["user"] = p["prompt_template"]["user"].replace(
                "Approval decision: {{approval}}",
                "Autonomy: hands-off. No human approval: deploy-production runs automatically after the "
                "release; play-job simply waits for it.")
        if p["prompt_id"] == "downstream_gatekeeper":
            p["prompt_template"]["user"] = p["prompt_template"]["user"].replace(
                "Your final answer is shown to the human who approves production, so make it",
                "Your final answer goes to the deployer agent, so make it")
    return flow


def render(flow: dict) -> str:
    return HEADER + yaml.safe_dump(derive(flow), sort_keys=False, width=110, allow_unicode=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = render(yaml.safe_load(SRC.read_text(encoding="utf-8")))
    if args.check:
        if not DST.exists() or DST.read_text(encoding="utf-8") != text:
            print(f"{DST.relative_to(ROOT)} is out of date: run python scripts/build_flows.py")
            return 1
        return 0
    DST.write_text(text, encoding="utf-8")
    print(f"wrote {DST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
