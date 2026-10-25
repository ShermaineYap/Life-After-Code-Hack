"""Software Carbon Intensity (SCI) for a data-pipeline build.

Implements the Green Software Foundation SCI specification
(ISO/IEC 21031:2024):

    SCI = ((E * I) + M) / R

* E  energy of the build in kWh, measured from dbt's own run_results.json:
     wall-clock elapsed time x (vCPU power + memory power) x PUE.
* I  location-based grid intensity of the region (gCO2e/kWh).
* M  embodied emissions of the runner hardware allocated to this build:
     TE x (time reserved / expected lifespan) x (vCPUs reserved / host vCPUs).
* R  functional unit: one release of the data product.

Coefficients (all overridable by flags):
* vCPU power interpolated between 0.71 W (idle) and 4.26 W (100%) per vCPU,
  memory 0.392 W/GB, PUE 1.1 -- Cloud Carbon Footprint's GCP coefficients,
  https://www.cloudcarbonfootprint.org/docs/methodology/
* Grid intensity per region from Google Cloud's 2025 region data,
  https://cloud.google.com/sustainability/region-carbon
* Embodied: 1,200 kgCO2e per server, 4-year life, 48 vCPU host (assumptions,
  stated in the output).

Compare two builds (e.g. this merge request's slim build against the last
full build on main) with --baseline. The comparison is what the Lineage
Reviewer uses to justify a slim rebuild, and what the release evidence shows.

Usage:
    python scripts/sci.py --run-results target/run_results.json --label slim \
        [--baseline state/sci.json] --json reports/sci.json --markdown reports/sci.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Google Cloud 2025 grid carbon intensity, gCO2e/kWh (location-based).
GRID_INTENSITY = {
    "asia-southeast1": 366,   # Singapore
    "asia-southeast2": 588,   # Jakarta
    "us-central1": 432,       # Iowa
    "europe-west1": 126,      # Belgium
    "europe-north1": 30,      # Finland
}
W_IDLE, W_MAX = 0.71, 4.26      # per vCPU, CCF GCP median
W_PER_GB = 0.392                # memory
PUE = 1.1
EMBODIED_KG = 1200.0            # per server
LIFESPAN_S = 4 * 365 * 24 * 3600
HOST_VCPUS = 48


def compute(elapsed_s: float, *, vcpus: float = 2, memory_gb: float = 4,
            utilisation: float = 0.5, region: str = "asia-southeast1",
            intensity: float | None = None, nodes: int = 0, label: str = "build") -> dict:
    watts = vcpus * (W_IDLE + utilisation * (W_MAX - W_IDLE)) + memory_gb * W_PER_GB
    energy_kwh = elapsed_s * watts * PUE / 3_600_000
    i = intensity if intensity is not None else GRID_INTENSITY.get(region, 366)
    operational_g = energy_kwh * i
    embodied_g = EMBODIED_KG * 1000 * (elapsed_s / LIFESPAN_S) * (vcpus / HOST_VCPUS)
    sci = operational_g + embodied_g  # R = 1 release
    return {
        "label": label,
        "functional_unit": "1 release",
        "nodes_built": nodes,
        "elapsed_s": round(elapsed_s, 3),
        "energy_kwh": energy_kwh,
        "grid_intensity_g_per_kwh": i,
        "region": region,
        "operational_gco2e": operational_g,
        "embodied_gco2e": embodied_g,
        "sci_gco2e_per_release": sci,
        "assumptions": {
            "vcpus": vcpus, "memory_gb": memory_gb, "cpu_utilisation": utilisation,
            "watts_per_vcpu_idle_max": [W_IDLE, W_MAX], "watts_per_gb": W_PER_GB, "pue": PUE,
            "embodied_kgco2e_per_server": EMBODIED_KG, "server_lifespan_years": 4,
            "host_vcpus": HOST_VCPUS,
            "sources": [
                "https://sci-guide.greensoftware.foundation/",
                "https://www.cloudcarbonfootprint.org/docs/methodology/",
                "https://cloud.google.com/sustainability/region-carbon",
            ],
        },
    }


def from_run_results(path: str) -> tuple[float, int]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    built = sum(1 for r in data.get("results", [])
                if r.get("unique_id", "").startswith(("model.", "seed.")) and r.get("status") != "skipped")
    return float(data.get("elapsed_time") or 0.0), built


def fmt_g(x: float) -> str:
    return f"{x * 1000:.3f} mg" if x < 1 else f"{x:.3f} g"


def to_markdown(cur: dict, base: dict | None, cleaner_region: dict | None) -> str:
    lines = ["## Software Carbon Intensity (SCI)", "",
             f"**{fmt_g(cur['sci_gco2e_per_release'])} CO2e per release** for this {cur['label']} build "
             f"({cur['nodes_built']} models/seeds, {cur['elapsed_s']:.1f} s, "
             f"{cur['region']} at {cur['grid_intensity_g_per_kwh']} gCO2e/kWh).", ""]
    rows = [cur] + ([base] if base else []) + ([cleaner_region] if cleaner_region else [])
    lines += ["| Build | Models | Time (s) | Energy (Wh) | Operational | Embodied | SCI / release |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['label']} | {r['nodes_built']} | {r['elapsed_s']:.2f} | "
                     f"{r['energy_kwh'] * 1000:.4f} | {fmt_g(r['operational_gco2e'])} | "
                     f"{fmt_g(r['embodied_gco2e'])} | {fmt_g(r['sci_gco2e_per_release'])} |")
    if base and base["sci_gco2e_per_release"] > 0:
        saving = 1 - cur["sci_gco2e_per_release"] / base["sci_gco2e_per_release"]
        lines += ["", f"**{saving:.0%} lower SCI** than the `{base['label']}` baseline."]
    lines += ["", "_Method: GSF SCI (ISO/IEC 21031:2024); energy from dbt run_results elapsed time; "
              "Cloud Carbon Footprint GCP coefficients; Google Cloud 2025 regional grid intensity._"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-results", default="target/run_results.json")
    ap.add_argument("--label", default="build")
    ap.add_argument("--elapsed", type=float, help="override measured seconds")
    ap.add_argument("--vcpus", type=float, default=2)
    ap.add_argument("--memory-gb", type=float, default=4)
    ap.add_argument("--utilisation", type=float, default=0.5)
    ap.add_argument("--region", default="asia-southeast1")
    ap.add_argument("--baseline", help="sci.json of the build to compare against")
    ap.add_argument("--compare-region", default="europe-north1",
                    help="what-if region for the carbon-aware scheduling row ('' to disable)")
    ap.add_argument("--json", dest="json_out", default="reports/sci.json")
    ap.add_argument("--markdown", dest="md_out", default="reports/sci.md")
    args = ap.parse_args(argv)

    elapsed, nodes = (0.0, 0)
    if Path(args.run_results).exists():
        elapsed, nodes = from_run_results(args.run_results)
    if args.elapsed is not None:
        elapsed = args.elapsed
    common = dict(vcpus=args.vcpus, memory_gb=args.memory_gb, utilisation=args.utilisation, nodes=nodes)
    cur = compute(elapsed, region=args.region, label=args.label, **common)

    base = None
    if args.baseline and Path(args.baseline).exists():
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8")).get("current")
        if base:
            base = dict(base, label=f"{base['label']} (baseline)")
    what_if = None
    if args.compare_region and args.compare_region != args.region:
        what_if = compute(elapsed, region=args.compare_region,
                          label=f"{args.label} in {args.compare_region} (what-if)", **common)

    report = {"current": cur, "baseline": base, "what_if_region": what_if}
    if base and base["sci_gco2e_per_release"] > 0:
        report["reduction_vs_baseline"] = 1 - cur["sci_gco2e_per_release"] / base["sci_gco2e_per_release"]
    for path, text in ((args.json_out, json.dumps(report, indent=2)),
                       (args.md_out, to_markdown(cur, base, what_if))):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    print(to_markdown(cur, base, what_if))
    return 0


if __name__ == "__main__":
    sys.exit(main())
