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


def test_merge_requests_auto_merge(monkeypatch, calls):
    seen, replies = calls
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    replies["/merge_requests/7"] = {"title": "feat: x", "draft": False}
    replies["/merge_requests/7/merge"] = {"state": "opened", "merge_when_pipeline_succeeds": True}
    assert ds.main(["--project", "1", "merge-mr", "--mr", "7"]) == 0
    assert [m for m, *_ in seen] == ["GET", "PUT"]
    method, url, _, body = seen[-1]
    assert method == "PUT" and url.endswith("/merge_requests/7/merge")
    assert body["auto_merge"] is True and body["should_remove_source_branch"] is True


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
