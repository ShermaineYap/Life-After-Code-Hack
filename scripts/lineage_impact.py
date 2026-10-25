"""Lineage-aware review of a dbt change: blast radius, contract breaks, test gaps.

Compares the manifest of the change under review with the manifest of the
default branch ("state") and reports:

* which models changed, were added or removed;
* every downstream model and dashboard tile (dbt exposure) the change reaches;
* columns removed or renamed in a changed model, and which downstream models
  still reference them in their SQL (a contract break that dbt only discovers
  at run time, after the release);
* new or changed models whose columns carry no tests;
* the slim-build selector and how much of the project it rebuilds, which the
  SCI step uses to quantify the carbon saved by not doing a full refresh.

Usage:
    python scripts/lineage_impact.py \
        --manifest target/manifest.json \
        --state-manifest state/manifest.json \
        [--catalog target/catalog.json --state-catalog state/catalog.json] \
        --json reports/lineage.json --markdown reports/lineage.md \
        [--fail-on-breaking]

Exit codes: 0 = ok, 2 = breaking change found and --fail-on-breaking set.
Without --state-manifest every model is treated as new (first release).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path

KEY_HINTS = ("_id", "_month")  # columns a dashboard joins or groups on


def load(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def models_of(manifest: dict) -> dict[str, dict]:
    return {k: v for k, v in manifest.get("nodes", {}).items()
            if v.get("resource_type") == "model"}


def columns_of(uid: str, manifest: dict, catalog: dict | None) -> list[str]:
    """Real columns from the warehouse catalog when available, else the YAML."""
    if catalog and uid in catalog.get("nodes", {}):
        cols = catalog["nodes"][uid].get("columns", {})
        return [c.lower() for c in sorted(cols, key=lambda c: cols[c].get("index", 0))]
    node = manifest.get("nodes", {}).get(uid, {})
    return [c.lower() for c in node.get("columns", {})]


def descendants(uid: str, manifest: dict) -> set[str]:
    child_map = manifest.get("child_map", {})
    seen, queue = set(), deque([uid])
    while queue:
        for child in child_map.get(queue.popleft(), []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def tests_by_model(manifest: dict) -> dict[str, dict[str | None, list[str]]]:
    """{model_uid: {column_name_or_None: [test names]}}"""
    out: dict[str, dict[str | None, list[str]]] = {}
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "test":
            continue
        targets = [node["attached_node"]] if node.get("attached_node") else [
            d for d in node.get("depends_on", {}).get("nodes", []) if d.startswith("model.")]
        col = (node.get("column_name") or None)
        name = (node.get("test_metadata") or {}).get("name") or node.get("name", "test")
        for t in targets:
            out.setdefault(t, {}).setdefault(col.lower() if col else None, []).append(name)
    return out


def references_column(sql: str, column: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(column)}(?![A-Za-z0-9_])", sql, re.I) is not None


def analyse(manifest: dict, state: dict | None,
            catalog: dict | None = None, state_catalog: dict | None = None) -> dict:
    new_models = models_of(manifest)
    old_models = models_of(state) if state else {}

    added = sorted(set(new_models) - set(old_models))
    removed = sorted(set(old_models) - set(new_models))
    modified = sorted(
        uid for uid in set(new_models) & set(old_models)
        if new_models[uid]["checksum"]["checksum"] != old_models[uid]["checksum"]["checksum"]
        or columns_of(uid, manifest, None) != columns_of(uid, state, None)
    )
    changed = added + modified

    impacted_models: set[str] = set()
    impacted_exposures: set[str] = set()
    for uid in changed:
        for d in descendants(uid, manifest):
            if d.startswith("model."):
                impacted_models.add(d)
            elif d.startswith("exposure."):
                impacted_exposures.add(d)
    impacted_models -= set(changed)

    # Contract breaks: columns that disappeared from a modified model but are
    # still referenced by a direct child's SQL.
    breaking = []
    schema_changes = []
    child_map = manifest.get("child_map", {})
    for uid in modified:
        before = columns_of(uid, state, state_catalog)
        after = columns_of(uid, manifest, catalog)
        gone = [c for c in before if c not in after]
        new = [c for c in after if c not in before]
        if gone or new:
            schema_changes.append({"model": new_models[uid]["name"], "removed": gone, "added": new})
        for col in gone:
            for child in child_map.get(uid, []):
                if not child.startswith("model."):
                    continue
                code = new_models[child].get("raw_code", "")
                if references_column(code, col):
                    breaking.append({
                        "model": new_models[uid]["name"],
                        "column": col,
                        "referenced_by": new_models[child]["name"],
                        "file": new_models[child]["original_file_path"],
                        "suggested_fix": (
                            f"Either keep `{col}` in {new_models[uid]['name']} "
                            f"(alias the new name back), or update {new_models[child]['name']} "
                            f"to the new column name in the same merge request."),
                    })
    for uid in removed:
        for child in (state or {}).get("child_map", {}).get(uid, []):
            if child.startswith("model.") and child in new_models:
                breaking.append({
                    "model": old_models[uid]["name"], "column": "*",
                    "referenced_by": new_models[child]["name"],
                    "file": new_models[child]["original_file_path"],
                    "suggested_fix": "A removed model is still referenced downstream.",
                })

    # Test coverage for added and modified models.
    tests = tests_by_model(manifest)
    test_gaps = []
    for uid in changed:
        cols = columns_of(uid, manifest, catalog)
        tested = tests.get(uid, {})
        # Untested descriptive columns only matter on genuinely new models;
        # on the first release everything is "new", so only keys are checked.
        untested = ([c for c in cols if c not in tested]
                    if uid in added and state is not None else [])
        key_cols = [c for c in cols if c.endswith(KEY_HINTS) or c == "month"] or cols[:1]
        key_untested = [c for c in key_cols if not ({"unique", "not_null"} & set(tested.get(c, [])))]
        undocumented = not new_models[uid].get("columns")
        if untested or key_untested or undocumented:
            test_gaps.append({
                "model": new_models[uid]["name"],
                "file": new_models[uid]["original_file_path"],
                "undocumented": undocumented,
                "untested_columns": untested,
                "key_columns_missing_unique_or_not_null": key_untested,
            })

    exposures = manifest.get("exposures", {})
    selected = set(changed) | impacted_models
    total = len(new_models)
    return {
        "summary": {
            "models_total": total,
            "models_added": len(added),
            "models_modified": len(modified),
            "models_removed": len(removed),
            "downstream_models": len(impacted_models),
            "dashboard_tiles": len(impacted_exposures),
            "breaking_changes": len(breaking),
            "models_with_test_gaps": len(test_gaps),
            "slim_build_models": len(selected),
            "slim_build_share": round(len(selected) / total, 3) if total else 0.0,
            "first_release": state is None,
        },
        "added": [new_models[u]["name"] for u in added],
        "modified": [new_models[u]["name"] for u in modified],
        "removed": [old_models[u]["name"] for u in removed],
        "downstream_models": sorted(new_models[u]["name"] for u in impacted_models),
        "dashboard_tiles": sorted(
            ({"name": exposures[e]["name"], "label": exposures[e].get("label") or exposures[e]["name"],
              "owner": (exposures[e].get("owner") or {}).get("name")}
             for e in impacted_exposures if e in exposures),
            key=lambda x: x["name"]),
        "schema_changes": schema_changes,
        "breaking_changes": breaking,
        "test_gaps": test_gaps,
        "slim_selector": "state:modified+",
    }


def to_markdown(r: dict) -> str:
    s = r["summary"]
    verdict = ("**Blocking: contract break found.**" if s["breaking_changes"]
               else "No contract breaks found.")
    lines = [
        "## Downstream lineage review",
        "",
        verdict,
        "",
        "| Changed | Downstream models | Dashboard tiles | Breaking | Test gaps | Slim build |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| {s['models_added']} added, {s['models_modified']} modified, {s['models_removed']} removed "
        f"| {s['downstream_models']} | {s['dashboard_tiles']} | {s['breaking_changes']} "
        f"| {s['models_with_test_gaps']} | {s['slim_build_models']}/{s['models_total']} models "
        f"({s['slim_build_share']:.0%}) |",
        "",
    ]
    if r["added"] or r["modified"]:
        lines += ["**Changed models:** " + ", ".join(f"`{m}`" for m in r["added"] + r["modified"]), ""]
    if r["downstream_models"]:
        lines += ["**Blast radius:** " + ", ".join(f"`{m}`" for m in r["downstream_models"]), ""]
    if r["dashboard_tiles"]:
        lines += ["**Dashboard tiles affected:**", ""]
        lines += [f"- {t['label']} (owner: {t['owner'] or 'unknown'})" for t in r["dashboard_tiles"]]
        lines += [""]
    if r["schema_changes"]:
        lines += ["**Schema changes:**", ""]
        for c in r["schema_changes"]:
            parts = []
            if c["removed"]:
                parts.append("removed " + ", ".join(f"`{x}`" for x in c["removed"]))
            if c["added"]:
                parts.append("added " + ", ".join(f"`{x}`" for x in c["added"]))
            lines.append(f"- `{c['model']}`: " + "; ".join(parts))
        lines += [""]
    if r["breaking_changes"]:
        lines += ["**Contract breaks:**", ""]
        for b in r["breaking_changes"]:
            lines.append(f"- `{b['model']}.{b['column']}` is still used by `{b['referenced_by']}` "
                         f"({b['file']}). {b['suggested_fix']}")
        lines += [""]
    if r["test_gaps"]:
        lines += ["**Missing tests:**", ""]
        for g in r["test_gaps"]:
            msg = []
            if g.get("undocumented"):
                msg.append("not described in any YAML file: add it with a description and column tests")
            if g["key_columns_missing_unique_or_not_null"]:
                msg.append("key columns without unique/not_null: "
                           + ", ".join(f"`{c}`" for c in g["key_columns_missing_unique_or_not_null"]))
            if g["untested_columns"]:
                msg.append("untested: " + ", ".join(f"`{c}`" for c in g["untested_columns"]))
            lines.append(f"- `{g['model']}` — " + "; ".join(msg))
        lines += [""]
    lines.append(f"_Slim rebuild selector: `{r['slim_selector']}`._")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default="target/manifest.json")
    ap.add_argument("--state-manifest")
    ap.add_argument("--catalog")
    ap.add_argument("--state-catalog")
    ap.add_argument("--json", dest="json_out", default="reports/lineage.json")
    ap.add_argument("--markdown", dest="md_out", default="reports/lineage.md")
    ap.add_argument("--fail-on-breaking", action="store_true")
    args = ap.parse_args(argv)

    manifest = load(args.manifest)
    if manifest is None:
        print(f"manifest not found: {args.manifest} (run `dbt parse` first)", file=sys.stderr)
        return 1
    report = analyse(manifest, load(args.state_manifest),
                     load(args.catalog), load(args.state_catalog))

    for path, text in ((args.json_out, json.dumps(report, indent=2)), (args.md_out, to_markdown(report))):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    print(to_markdown(report))
    if args.fail_on_breaking and report["summary"]["breaking_changes"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
