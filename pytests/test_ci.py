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


def test_release_only_after_staging_is_verified():
    assert "monitor-staging" in JOBS["prepare-release"]["needs"]
    assert JOBS["release"]["image"].startswith("registry.gitlab.com/gitlab-org/cli")
