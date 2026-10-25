"""ds: the Downstream command-line bridge between the Duo flow and GitLab.

Duo flow agents call this through ``run_command`` (``python scripts/ds.py ...``)
for the steps their built-in tools don't cover: waiting on pipelines,
auto-merging, pressing the production deploy button, reading CI artefacts and
opening incidents. CI jobs use it too. Standard library only.

Authentication, first match wins:
    GITLAB_TOKEN / GITLAB_OAUTH_TOKEN   (inside a Duo flow)  -> Authorization: Bearer
    GITLAB_PRIVATE_TOKEN                (a person, locally)  -> PRIVATE-TOKEN
    CI_JOB_TOKEN                        (inside a CI job)    -> JOB-TOKEN
API base: CI_API_V4_URL, else $GITLAB_BASE_URL/api/v4, else https://gitlab.com/api/v4.
Project: --project, else CI_PROJECT_ID, else CI_PROJECT_PATH.

Every command prints one JSON object on stdout so an agent can read it.

Examples:
    python scripts/ds.py wait-mr-pipeline --mr 12
    python scripts/ds.py merge-mr --mr 12
    python scripts/ds.py wait-pipeline --ref main --sha 1a2b3c4
    python scripts/ds.py play-job --pipeline 9876 --job deploy-prod --wait
    python scripts/ds.py job-artifact --pipeline 9876 --job monitor-prod --path reports/monitor.md
    python scripts/ds.py create-incident --title "Freshness broke" --body-file reports/monitor.md
    python scripts/ds.py comment-issue --issue 7 --body-file reports/summary.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TERMINAL = {"success", "failed", "canceled", "skipped", "manual"}
DEFAULT_BRANCH = os.environ.get("CI_DEFAULT_BRANCH", "main")


class GitLab:
    def __init__(self, project: str | None = None) -> None:
        base = os.environ.get("CI_API_V4_URL")
        if not base:
            host = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com").rstrip("/")
            base = f"{host}/api/v4"
        self.base = base.rstrip("/")
        proj = project or os.environ.get("CI_PROJECT_ID") or os.environ.get("CI_PROJECT_PATH")
        if not proj:
            raise SystemExit(_out({"error": "no project: pass --project or set CI_PROJECT_ID"}, code=2))
        self.project = urllib.parse.quote(str(proj), safe="")
        self.headers = {"Accept": "application/json"}
        if os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_OAUTH_TOKEN"):
            tok = os.environ.get("GITLAB_TOKEN") or os.environ["GITLAB_OAUTH_TOKEN"]
            self.headers["Authorization"] = f"Bearer {tok}"
        elif os.environ.get("GITLAB_PRIVATE_TOKEN"):
            self.headers["PRIVATE-TOKEN"] = os.environ["GITLAB_PRIVATE_TOKEN"]
        elif os.environ.get("CI_JOB_TOKEN"):
            self.headers["JOB-TOKEN"] = os.environ["CI_JOB_TOKEN"]

    def request(self, method: str, path: str, data: dict | None = None, raw: bool = False):
        url = f"{self.base}/projects/{self.project}{path}"
        body = None
        headers = dict(self.headers)
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and method == "GET" and len(headers) > 1:
                # A token without this API in its scope (a Duo flow's token, for some
                # endpoints) can still read a public project anonymously.
                anon = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
                try:
                    with urllib.request.urlopen(anon, timeout=60) as resp:
                        payload = resp.read()
                except urllib.error.HTTPError:
                    payload = None
                if payload is not None:
                    return payload if raw else (json.loads(payload.decode("utf-8")) if payload else {})
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
        if raw:
            return payload
        return json.loads(payload.decode("utf-8")) if payload else {}

    def get(self, path: str):
        return self.request("GET", path)

    # -- pipelines -----------------------------------------------------------------
    def wait_pipeline(self, pipeline_id: int, timeout: int, poll: int) -> dict:
        deadline = time.time() + timeout
        while True:
            p = self.get(f"/pipelines/{pipeline_id}")
            if p["status"] in TERMINAL or time.time() > deadline:
                return p
            time.sleep(poll)

    def failing_jobs(self, pipeline_id: int) -> list[dict]:
        jobs = self.get(f"/pipelines/{pipeline_id}/jobs?scope[]=failed&per_page=50")
        return [{"id": j["id"], "name": j["name"], "stage": j["stage"], "web_url": j["web_url"],
                 "allow_failure": j.get("allow_failure", False)} for j in jobs]

    def job_by_name(self, pipeline_id: int, name: str) -> dict | None:
        for j in self.get(f"/pipelines/{pipeline_id}/jobs?per_page=100&include_retried=false"):
            if j["name"] == name:
                return j
        return None

    def wait_job(self, job_id: int, timeout: int, poll: int) -> dict:
        deadline = time.time() + timeout
        while True:
            j = self.get(f"/jobs/{job_id}")
            if j["status"] in TERMINAL - {"manual"} or time.time() > deadline:
                return j
            time.sleep(poll)


def _out(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return code


def cmd_wait_mr_pipeline(gl: GitLab, a) -> int:
    """Wait for the pipeline of the merge request's CURRENT head commit.

    Right after a push, the newest pipeline can still be the previous commit's;
    a stale green result must never be mistaken for the new one.
    """
    deadline = time.time() + a.timeout
    head = gl.get(f"/merge_requests/{a.mr}").get("sha")
    current = None
    while current is None and time.time() < deadline:
        for p in gl.get(f"/merge_requests/{a.mr}/pipelines"):
            if p.get("sha") == head:
                current = p
                break
        if current is None:
            time.sleep(a.poll)
    if current is None:
        return _out({"status": "none", "head_sha": head,
                     "detail": "no pipeline yet for the merge request's head commit"}, 1)
    p = gl.wait_pipeline(current["id"], int(max(deadline - time.time(), 0)), a.poll)
    res = {"pipeline_id": p["id"], "status": p["status"], "web_url": p["web_url"], "sha": p["sha"]}
    if p["status"] == "failed":
        res["failing_jobs"] = gl.failing_jobs(p["id"])
    return _out(res, 0 if p["status"] == "success" else 1)


def cmd_wait_pipeline(gl: GitLab, a) -> int:
    deadline = time.time() + a.timeout
    p = None
    while p is None and time.time() < deadline:
        q = f"/pipelines?ref={urllib.parse.quote(a.ref)}&per_page=5&order_by=id&sort=desc"
        if a.sha:
            q += f"&sha={a.sha}"
        found = gl.get(q)
        p = found[0] if found else None
        if p is None:
            time.sleep(a.poll)
    if p is None:
        return _out({"status": "none", "detail": f"no pipeline for ref={a.ref} sha={a.sha}"}, 1)
    if a.until_job:
        # Wait until a named job settles (e.g. deploy-prod reaches 'manual').
        while time.time() < deadline:
            j = gl.job_by_name(p["id"], a.until_job)
            if j and j["status"] in TERMINAL:
                return _out({"pipeline_id": p["id"], "web_url": p["web_url"], "sha": p["sha"],
                             "job": a.until_job, "job_id": j["id"], "job_status": j["status"]},
                            0 if j["status"] in {"success", "manual"} else 1)
            if gl.get(f"/pipelines/{p['id']}")["status"] in {"failed", "canceled"}:
                break
            time.sleep(a.poll)
        p = gl.get(f"/pipelines/{p['id']}")
        res = {"pipeline_id": p["id"], "status": p["status"], "web_url": p["web_url"],
               "detail": f"{a.until_job} did not settle"}
        if p["status"] == "failed":
            res["failing_jobs"] = gl.failing_jobs(p["id"])
        return _out(res, 1)
    p = gl.wait_pipeline(p["id"], int(max(deadline - time.time(), 0)), a.poll)
    res = {"pipeline_id": p["id"], "status": p["status"], "web_url": p["web_url"], "sha": p["sha"]}
    if p["status"] == "failed":
        res["failing_jobs"] = gl.failing_jobs(p["id"])
    return _out(res, 0 if p["status"] in {"success", "manual"} else 1)


def cmd_merge_mr(gl: GitLab, a) -> int:
    mr = gl.get(f"/merge_requests/{a.mr}")
    if mr.get("draft") or mr.get("work_in_progress"):
        gl.request("PUT", f"/merge_requests/{a.mr}", {"title": mr["title"].removeprefix("Draft: ")})
    payload = {"should_remove_source_branch": True, "squash": a.squash}
    if not a.now:
        payload["auto_merge"] = True
        payload["merge_when_pipeline_succeeds"] = True
    try:
        res = gl.request("PUT", f"/merge_requests/{a.mr}/merge", payload)
    except RuntimeError as exc:
        if "403" not in str(exc):
            return _out({"merged": False, "error": str(exc)}, 1)
        # A flow's token may not merge (403 insufficient_scope). Hand the button to a person
        # and wait for them; never work around the permission.
        gl.request("POST", f"/merge_requests/{a.mr}/notes", {"body": (
            "Downstream: the pipeline is green and this merge request is ready, but this flow is not "
            "permitted to merge it. A maintainer, please press **Merge**; the release continues "
            "automatically once it lands.")})
        res = {"state": mr.get("state"), "handed_to_human": True}
    # Auto-merge happens later: wait until it lands so the caller gets the merge SHA.
    deadline = time.time() + a.timeout
    while res.get("state") != "merged" and time.time() < deadline:
        time.sleep(a.poll)
        res = gl.get(f"/merge_requests/{a.mr}")
        if res.get("state") == "closed":
            break
    sha = res.get("merge_commit_sha") or res.get("squash_commit_sha")
    return _out({"merged": res.get("state") == "merged", "state": res.get("state"),
                 "merge_commit_sha": sha, "web_url": res.get("web_url")},
                0 if res.get("state") == "merged" else 1)


def cmd_play_job(gl: GitLab, a) -> int:
    job = gl.job_by_name(a.pipeline, a.job)
    if not job:
        return _out({"error": f"job {a.job} not found in pipeline {a.pipeline}"}, 1)
    if job["status"] == "manual":
        job = gl.request("POST", f"/jobs/{job['id']}/play", {})
    if a.wait:
        job = gl.wait_job(job["id"], a.timeout, a.poll)
    return _out({"job_id": job["id"], "name": job["name"], "status": job["status"],
                 "web_url": job["web_url"]}, 0 if job["status"] in {"success", "pending", "running", "created"} else 1)


def cmd_job_status(gl: GitLab, a) -> int:
    job = gl.job_by_name(a.pipeline, a.job)
    if not job:
        return _out({"error": f"job {a.job} not found in pipeline {a.pipeline}"}, 1)
    if a.wait:
        job = gl.wait_job(job["id"], a.timeout, a.poll)
    return _out({"job_id": job["id"], "name": job["name"], "status": job["status"],
                 "web_url": job["web_url"]}, 0 if job["status"] == "success" else 1)


def latest_successful_job(gl: GitLab, ref: str, name: str, scan: int = 20) -> dict | None:
    """Newest successful job called `name` on `ref`.

    Not GitLab's "artifacts of the latest successful pipeline" lookup: in
    Supervised mode every default-branch pipeline stops at the manual production
    gate (status "manual"), so there may be no "successful" pipeline at all even
    though its build job succeeded.
    """
    q = urllib.parse.quote(ref, safe="")
    for p in gl.get(f"/pipelines?ref={q}&per_page={scan}&order_by=id&sort=desc"):
        for j in gl.get(f"/pipelines/{p['id']}/jobs?per_page=100&scope[]=success"):
            if j["name"] == name:
                return j
    return None


def cmd_job_artifact(gl: GitLab, a) -> int:
    try:
        job = gl.job_by_name(a.pipeline, a.job) if a.pipeline else latest_successful_job(gl, a.ref, a.job)
        if not job:
            where = f"pipeline {a.pipeline}" if a.pipeline else f"recent {a.ref} pipelines"
            return _out({"error": f"no successful job {a.job} in {where}"}, 1)
        data = gl.request("GET", f"/jobs/{job['id']}/artifacts/{a.path}", raw=True)
    except RuntimeError as exc:
        return _out({"error": str(exc)}, 1)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_bytes(data)
        return _out({"saved": a.output, "bytes": len(data), "job_id": job["id"]})
    sys.stdout.write(data.decode("utf-8", "replace"))
    return 0


def _body(a) -> str:
    if a.body_file and Path(a.body_file).exists():
        return Path(a.body_file).read_text(encoding="utf-8")
    return a.body or (f"(report file {a.body_file} was not produced)" if a.body_file else "")


def cmd_create_incident(gl: GitLab, a) -> int:
    body = _body(a)
    res = gl.request("POST", "/issues", {"title": a.title, "description": body,
                                         "issue_type": "incident", "labels": a.labels})
    return _out({"incident_iid": res.get("iid"), "web_url": res.get("web_url")})


def cmd_comment_issue(gl: GitLab, a) -> int:
    body = _body(a)
    res = gl.request("POST", f"/issues/{a.issue}/notes", {"body": body})
    return _out({"note_id": res.get("id")})


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Downstream GitLab bridge")
    ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def timed(p):
        p.add_argument("--timeout", type=int, default=1500)
        p.add_argument("--poll", type=int, default=15)
        return p

    p = timed(sub.add_parser("wait-mr-pipeline", help="wait for a merge request's latest pipeline"))
    p.add_argument("--mr", type=int, required=True)

    p = timed(sub.add_parser("wait-pipeline", help="wait for a branch pipeline (or one of its jobs)"))
    p.add_argument("--ref", default=DEFAULT_BRANCH)
    p.add_argument("--sha")
    p.add_argument("--until-job", help="return as soon as this job settles, e.g. deploy-production at 'manual'")

    p = timed(sub.add_parser("merge-mr", help="merge when the pipeline succeeds; waits and prints the merge SHA"))
    p.add_argument("--mr", type=int, required=True)
    p.add_argument("--now", action="store_true", help="merge immediately instead of auto-merge")
    p.add_argument("--squash", action="store_true")

    p = timed(sub.add_parser("play-job", help="press a manual job's play button"))
    p.add_argument("--pipeline", type=int, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--wait", action="store_true")

    p = timed(sub.add_parser("job-status", help="status of a named job in a pipeline"))
    p.add_argument("--pipeline", type=int, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--wait", action="store_true")

    p = sub.add_parser("job-artifact", help="read one artefact file from a job")
    p.add_argument("--pipeline", type=int, help="omit to use the newest successful job of that name on --ref")
    p.add_argument("--ref", default=DEFAULT_BRANCH)
    p.add_argument("--job", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--output")

    p = sub.add_parser("create-incident", help="open an incident issue")
    p.add_argument("--title", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--labels", default="downstream,data-incident")

    p = sub.add_parser("comment-issue", help="comment on an issue")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    return ap


COMMANDS = {
    "wait-mr-pipeline": cmd_wait_mr_pipeline, "wait-pipeline": cmd_wait_pipeline,
    "merge-mr": cmd_merge_mr, "play-job": cmd_play_job, "job-status": cmd_job_status,
    "job-artifact": cmd_job_artifact, "create-incident": cmd_create_incident,
    "comment-issue": cmd_comment_issue,
}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return COMMANDS[a.cmd](GitLab(a.project), a)
    except RuntimeError as exc:
        return _out({"error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main())
