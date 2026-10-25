import json

import pytest

import ds


class FakeResp:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def calls(monkeypatch):
    seen = []
    replies = {}

    def fake_urlopen(req, timeout=60):
        body = json.loads(req.data) if req.data else None
        seen.append((req.get_method(), req.full_url, dict(req.header_items()), body))
        path = req.full_url.split("?")[0]
        matches = [k for k in replies if path.endswith(k)]
        return FakeResp(replies[max(matches, key=len)] if matches else {})

    monkeypatch.setattr(ds.urllib.request, "urlopen", fake_urlopen)
    for var in ("GITLAB_TOKEN", "GITLAB_OAUTH_TOKEN", "GITLAB_PRIVATE_TOKEN", "CI_JOB_TOKEN", "CI_API_V4_URL"):
        monkeypatch.delenv(var, raising=False)
    return seen, replies


def test_flow_token_wins_and_uses_bearer(monkeypatch, calls):
    monkeypatch.setenv("GITLAB_TOKEN", "flow")
    monkeypatch.setenv("CI_JOB_TOKEN", "job")
    gl = ds.GitLab("group/proj")
    assert gl.headers["Authorization"] == "Bearer flow" and "JOB-TOKEN" not in gl.headers
    assert gl.project == "group%2Fproj"
    assert gl.base == "https://gitlab.com/api/v4"


def test_merge_waits_for_auto_merge_and_returns_the_sha(monkeypatch, calls):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/merge_requests/7"] = {"title": "feat: x", "draft": False, "state": "opened"}
    replies["/merge_requests/7/merge"] = {"state": "opened", "merge_when_pipeline_succeeds": True}
    real_request = ds.GitLab.request

    def request(self, method, path, data=None, raw=False):
        res = real_request(self, method, path, data, raw)
        if method == "PUT":  # auto-merge queued; the next poll sees it merged
            replies["/merge_requests/7"] = {"state": "merged", "merge_commit_sha": "abc123"}
        return res

    monkeypatch.setattr(ds.GitLab, "request", request)
    assert ds.main(["--project", "1", "merge-mr", "--mr", "7", "--poll", "0", "--timeout", "5"]) == 0
    assert [m for m, *_ in seen] == ["GET", "PUT", "GET"]
    put = seen[1]
    assert put[1].endswith("/merge_requests/7/merge")
    assert put[3]["auto_merge"] is True and put[3]["should_remove_source_branch"] is True


def test_mr_pipeline_ignores_the_previous_commits_pipeline(monkeypatch, calls):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/merge_requests/3"] = {"sha": "new"}
    replies["/merge_requests/3/pipelines"] = [{"id": 1, "sha": "old", "status": "success"}]
    rc = ds.main(["--project", "1", "wait-mr-pipeline", "--mr", "3", "--timeout", "0", "--poll", "0"])
    assert rc == 1  # the green pipeline belongs to the old commit
    replies["/merge_requests/3/pipelines"] = [{"id": 2, "sha": "new"}, {"id": 1, "sha": "old"}]
    replies["/pipelines/2"] = {"id": 2, "sha": "new", "status": "success", "web_url": "u"}
    assert ds.main(["--project", "1", "wait-mr-pipeline", "--mr", "3", "--timeout", "5", "--poll", "0"]) == 0


def test_missing_body_file_still_opens_the_incident(monkeypatch, calls, tmp_path):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    assert ds.main(["--project", "1", "create-incident", "--title", "t",
                    "--body-file", str(tmp_path / "missing.md")]) == 0
    assert "was not produced" in seen[-1][3]["description"]


def test_create_incident_sets_issue_type(monkeypatch, calls, tmp_path):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/issues"] = {"iid": 42, "web_url": "https://x/42"}
    (tmp_path / "m.md").write_text("ANOMALY")
    assert ds.main(["--project", "1", "create-incident", "--title", "t", "--body-file", str(tmp_path / "m.md")]) == 0
    assert seen[-1][3]["issue_type"] == "incident" and seen[-1][3]["description"] == "ANOMALY"


def test_play_job_presses_the_manual_button(monkeypatch, calls):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/pipelines/5/jobs"] = [{"id": 9, "name": "deploy-production", "status": "manual", "web_url": "u"}]
    replies["/jobs/9/play"] = {"id": 9, "name": "deploy-production", "status": "pending", "web_url": "u"}
    assert ds.main(["--project", "1", "play-job", "--pipeline", "5", "--job", "deploy-production"]) == 0
    assert any(m == "POST" and u.endswith("/jobs/9/play") for m, u, _, _ in seen)


def test_missing_project_is_reported(monkeypatch, calls):
    monkeypatch.delenv("CI_PROJECT_ID", raising=False)
    monkeypatch.delenv("CI_PROJECT_PATH", raising=False)
    with pytest.raises(SystemExit):
        ds.GitLab(None)


def test_artifact_comes_from_newest_successful_job_even_when_pipelines_wait_at_the_gate(monkeypatch, calls, tmp_path):
    """Found live: main pipelines sit at 'manual', so "latest successful pipeline" had no artifacts."""
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/pipelines"] = [{"id": 30, "status": "manual"}, {"id": 20, "status": "failed"}]
    replies["/pipelines/30/jobs"] = [{"id": 301, "name": "dbt-build", "status": "success"}]
    replies["/jobs/301/artifacts/reports/sci.json"] = {"current": {"sci": 1}}
    out = tmp_path / "sci.json"
    assert ds.main(["--project", "1", "job-artifact", "--job", "dbt-build", "--path", "reports/sci.json",
                    "--output", str(out)]) == 0
    assert json.loads(out.read_text()) == {"current": {"sci": 1}}


def test_public_project_read_retries_anonymously_on_403(monkeypatch):
    calls_made = []

    class Resp(FakeResp):
        pass

    def fake_urlopen(req, timeout=60):
        calls_made.append(dict(req.header_items()))
        if "Authorization" in dict(req.header_items()):
            raise ds.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        return Resp({"ok": True})

    monkeypatch.setattr(ds.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    assert ds.GitLab("1").get("/pipelines") == {"ok": True}
    assert len(calls_made) == 2 and "Authorization" not in calls_made[1]
