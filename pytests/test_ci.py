"""Static checks on .gitlab-ci.yml: job graph, stages and the lifecycle it covers."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
JOBS = {k: v for k, v in CI.items() if isinstance(v, dict) and not k.startswith(".")
        and k not in {"workflow", "variables", "include", "default"}}


def _resolved(job: dict) -> dict:
    base = CI.get(job.get("extends", ""), {}) if isinstance(job.get("extends"), str) else {}
    return {**base, **job}


def test_every_job_has_a_known_stage():
    for name, job in JOBS.items():
        assert _resolved(job)["stage"] in CI["stages"], name


def test_needs_reference_real_jobs():
    for name, job in JOBS.items():
        for need in job.get("needs", []):
            if isinstance(need, dict) and need.get("optional"):
                continue  # template jobs (scanners) that may not exist in every pipeline
            need = need["job"] if isinstance(need, dict) else need
            assert need in JOBS, f"{name} needs unknown job {need}"


def test_security_templates_included():
    templates = {i["template"] for i in CI["include"]}
    assert templates == {"Jobs/SAST.gitlab-ci.yml", "Jobs/Secret-Detection.gitlab-ci.yml",
                         "Jobs/Dependency-Scanning.v2.gitlab-ci.yml"}
    assert "test" in CI["stages"]  # the templates' jobs run in 'test'


def test_production_gate_is_manual_unless_hands_off():
    rules = JOBS["deploy-production"]["rules"]
    manual = [r for r in rules if r.get("when") == "manual"]
    assert manual and manual[0]["allow_failure"] is False
    assert any("hands-off" in r.get("if", "") and "when" not in r for r in rules)


def test_rollbacks_follow_their_monitors():
    for env in ("staging", "production"):
        job = JOBS[f"rollback-{env}"]
        assert f"monitor-{env}" in job["needs"]
        assert any(r.get("when") == "on_failure" for r in job["rules"])


def test_release_only_after_staging_is_verified_and_scanned():
    needs = JOBS["prepare-release"]["needs"]
    assert "monitor-staging" in needs
    assert {n["job"] for n in needs if isinstance(n, dict)} == {"semgrep-sast", "secret_detection", "dependency-scanning"}


def test_script_lines_are_strings():
    for name, job in JOBS.items():
        for line in _resolved(job).get("script", []):
            assert isinstance(line, str), f"{name}: {line!r} parses as YAML, quote it"


def test_release_job_overrides_glab_entrypoint():
    image = JOBS["release"]["image"]
    assert image["name"].startswith("registry.gitlab.com/gitlab-org/cli")
    assert image["entrypoint"] == [""]


def test_version_step_fetches_tags_first():
    """Found live: runner clones have no tags, so the second release recomputed v1.0.0."""
    script = JOBS["lineage-review"]["script"]
    fetch = next(i for i, line in enumerate(script) if "git fetch --tags" in line)
    read = next(i for i, line in enumerate(script) if "git tag --list" in line)
    assert fetch < read
