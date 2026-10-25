"""Turn the pipeline's reports into a versioned data release.

Reads the lineage, PII and SCI reports produced earlier in the pipeline and
writes:

* ``reports/version.txt``   next semantic version of the data product;
* ``reports/release.md``    release notes written as a *data contract diff*;
* ``reports/evidence.json`` the evidence bundle attached to the release
                            (what changed, what was checked, what it cost).

Versioning rule (semantic versioning for a data contract):
    major  a contract model (meta.contract) lost a column, or a model was removed
    minor  a model or column was added
    patch  anything else (logic change, tests, docs)

Usage:
    python scripts/release_notes.py --previous v1.3.0 \
        --lineage reports/lineage.json --pii reports/pii.json --sci reports/sci.json \
        --manifest target/manifest.json --out-dir reports
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def load(path: str | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}


def contract_models(manifest: dict) -> set[str]:
    return {n["name"] for n in manifest.get("nodes", {}).values()
            if n.get("resource_type") == "model" and (n.get("meta") or n.get("config", {}).get("meta", {})).get("contract")}


def bump(previous: str, lineage: dict, contracts: set[str]) -> tuple[str, str]:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", previous or "")
    major, minor, patch = (int(x) for x in m.groups()) if m else (0, 0, 0)
    if not m:
        return "v1.0.0", "first release"
    removed_contract_cols = [c for c in lineage.get("schema_changes", [])
                             if c["model"] in contracts and c["removed"]]
    if lineage.get("removed") or removed_contract_cols:
        return f"v{major + 1}.0.0", "breaking: contract column or model removed"
    if lineage.get("added") or any(c["added"] for c in lineage.get("schema_changes", [])):
        return f"v{major}.{minor + 1}.0", "new model or column"
    return f"v{major}.{minor}.{patch + 1}", "logic, test or docs change"


def notes(version: str, reason: str, lineage: dict, pii: dict, sci: dict) -> str:
    s = lineage.get("summary", {})
    cur = sci.get("current") or {}
    lines = [f"# Data release {version}", "",
             f"Version bump: {reason}.", "", "## Data contract diff", ""]
    if lineage.get("added"):
        lines.append("- **New models:** " + ", ".join(f"`{m}`" for m in lineage["added"]))
    for c in lineage.get("schema_changes", []):
        if c["added"]:
            lines.append(f"- `{c['model']}` **added** " + ", ".join(f"`{x}`" for x in c["added"]))
        if c["removed"]:
            lines.append(f"- `{c['model']}` **removed** " + ", ".join(f"`{x}`" for x in c["removed"]))
    if lineage.get("removed"):
        lines.append("- **Removed models:** " + ", ".join(f"`{m}`" for m in lineage["removed"]))
    if lineage.get("modified") and not lineage.get("schema_changes"):
        lines.append("- Logic changed (no schema change) in " + ", ".join(f"`{m}`" for m in lineage["modified"]))
    if not any(line.startswith("- ") for line in lines):
        lines.append("- No schema changes.")
    tiles = lineage.get("dashboard_tiles", [])
    lines += ["", "## Who is affected", "",
              f"{s.get('downstream_models', 0)} downstream model(s) and {len(tiles)} dashboard tile(s):", ""]
    lines += [f"- {t['label']} (owner: {t.get('owner') or 'unknown'})" for t in tiles] or ["- none"]
    findings = pii.get("findings", [])
    lines += ["", "## Checks", "",
              f"- Lineage contract breaks: **{s.get('breaking_changes', 0)}**",
              f"- Models with test gaps: **{s.get('models_with_test_gaps', 0)}**",
              f"- PII outside raw schema: **{len([f for f in findings if f.get('severity') == 'high'])}** high, "
              f"{len([f for f in findings if f.get('severity') != 'high'])} medium",
              "- SAST, secret detection and dependency scanning: see this pipeline's security tab"]
    if cur:
        lines += ["", "## Carbon (SCI)", "",
                  f"{cur['sci_gco2e_per_release'] * 1000:.3f} mg CO2e per release "
                  f"({cur['nodes_built']} models/seeds built, {cur['elapsed_s']:.1f} s, {cur['region']})."]
        if sci.get("reduction_vs_baseline") is not None:
            lines.append(f"{sci['reduction_vs_baseline']:.0%} lower than the full-refresh baseline.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--previous", default=os.environ.get("PREVIOUS_VERSION", ""))
    ap.add_argument("--lineage", default="reports/lineage.json")
    ap.add_argument("--pii", default="reports/pii.json")
    ap.add_argument("--sci", default="reports/sci.json")
    ap.add_argument("--manifest", default="target/manifest.json")
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args(argv)

    lineage, pii, sci = load(args.lineage), load(args.pii), load(args.sci)
    version, reason = bump(args.previous, lineage, contract_models(load(args.manifest)))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "version.txt").write_text(version + "\n", encoding="utf-8")
    (out / "release.md").write_text(notes(version, reason, lineage, pii, sci), encoding="utf-8")
    evidence = {
        "version": version, "previous": args.previous or None, "bump_reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": os.environ.get("CI_COMMIT_SHA"), "pipeline": os.environ.get("CI_PIPELINE_URL"),
        "lineage": lineage.get("summary"), "pii_findings": pii.get("findings", []),
        "sci": sci.get("current"), "sci_reduction_vs_baseline": sci.get("reduction_vs_baseline"),
    }
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
