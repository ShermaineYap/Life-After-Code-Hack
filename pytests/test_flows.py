"""Static checks on the Duo flow YAML: the things that only fail once a flow runs."""

from pathlib import Path

import pytest
import yaml

import build_flows

ROOT = Path(__file__).resolve().parent.parent
FLOWS = sorted((ROOT / ".gitlab/duo/flows").glob("*.yaml"))

# Tool names from the Duo Workflow Service tool registry (ai-assist, duo_workflow_service/tools).
KNOWN_TOOLS = {
    "get_issue", "get_work_item", "list_issue_notes", "create_issue_note", "update_issue",
    "read_file", "read_files", "list_dir", "find_files", "grep", "edit_file",
    "create_file_with_contents", "mkdir", "run_command", "create_merge_request",
    "get_merge_request", "list_merge_request_diffs", "create_merge_request_note",
    "create_merge_request_diff_note", "get_pipeline_failing_jobs", "get_job_logs",
}
COMPONENT_TYPES = {"AgentComponent", "OneOffComponent", "DeterministicStepComponent", "HumanInputComponent"}
FORBIDDEN_TOP_LEVEL = {"name", "description", "product_group"}  # rejected for custom flows


@pytest.fixture(params=FLOWS, ids=lambda p: p.name)
def flow(request):
    return yaml.safe_load(request.param.read_text())


def test_flows_exist():
    assert {p.name for p in FLOWS} == {"downstream.yaml", "downstream-hands-off.yaml", "downstream-review.yaml"}


def test_top_level(flow):
    assert flow["version"] == "v1"
    assert flow["environment"] == "ambient"
    assert not FORBIDDEN_TOP_LEVEL & set(flow)
    names = [c["name"] for c in flow["components"]]
    assert len(names) == len(set(names))
    assert flow["flow"]["entry_point"] in names


def test_components(flow):
    prompts = {p["prompt_id"] for p in flow.get("prompts", [])}
    names = {c["name"] for c in flow["components"]}
    for c in flow["components"]:
        assert c["type"] in COMPONENT_TYPES
        assert ":" not in c["name"] and "." not in c["name"]
        if c["type"] == "AgentComponent":
            assert c["prompt_id"] in prompts, c["name"]
            # Rejected by GitLab.com's custom-flow schema ("disallowed additional property"),
            # found on the first live registration even though the v1 spec documents it.
            assert "max_cycles" not in c, c["name"]
            assert "prompt_version" not in c          # local prompts only
            assert "response_schema_id" not in c      # not supported in custom flows
            assert set(c.get("toolset", [])) <= KNOWN_TOOLS, set(c["toolset"]) - KNOWN_TOOLS
        if c["type"] == "HumanInputComponent":
            assert c["sends_response_to"] in names
        for i in c.get("inputs", []):
            src = i["from"] if isinstance(i, dict) else i
            if src.startswith("context:") and "." in src:
                assert src.split(":", 1)[1].split(".")[0] in names, src


def test_routers_connect_everything(flow):
    names = {c["name"] for c in flow["components"]}
    reachable, frontier = set(), [flow["flow"]["entry_point"]]
    edges = {}
    for r in flow["routers"]:
        assert r["from"] in names
        targets = list(r["condition"]["routes"].values()) if "condition" in r else [r["to"]]
        for t in targets:
            assert t in names | {"end", "abort"}, t
        edges.setdefault(r["from"], []).extend(targets)
    while frontier:
        n = frontier.pop()
        if n in reachable or n in {"end", "abort"}:
            continue
        reachable.add(n)
        frontier += edges.get(n, [])
    assert reachable == names, names - reachable


def test_prompts_are_well_formed(flow):
    for p in flow.get("prompts", []):
        assert {"prompt_id", "name", "unit_primitives", "prompt_template"} <= set(p)
        assert "model" not in p
        assert "stop" not in p.get("params", {})
        text = p["prompt_template"]["system"] + p["prompt_template"]["user"]
        assert "{project_id}" not in text.replace("{{project_id}}", "")


def test_long_commands_carry_a_tool_timeout(flow):
    """run_command stops a program after 120 s unless the agent passes a timeout."""
    for p in flow.get("prompts", []):
        system = p["prompt_template"]["system"]
        if "scripts/ds.py" in system or "downstream_review.sh" in system:
            assert "timeout" in system and "120 seconds" in system, p["prompt_id"]


def test_every_issue_tool_user_gets_the_project_id(flow):
    issue_tools = {"create_issue_note", "update_issue", "create_merge_request_note"}
    for c in flow["components"]:
        if issue_tools & set(c.get("toolset", [])):
            sources = [i["from"] if isinstance(i, dict) else i for i in c.get("inputs", [])]
            assert "context:project_id" in sources, c["name"]


def test_hands_off_is_generated_and_current():
    assert build_flows.main(["--check"]) == 0
    hands_off = yaml.safe_load((ROOT / ".gitlab/duo/flows/downstream-hands-off.yaml").read_text())
    assert "HumanInputComponent" not in {c["type"] for c in hands_off["components"]}
    assert {"from": "gatekeeper", "to": "deployer"} in hands_off["routers"]


def test_supervised_has_exactly_one_human_gate():
    sup = yaml.safe_load((ROOT / ".gitlab/duo/flows/downstream.yaml").read_text())
    gates = [c for c in sup["components"] if c["type"] == "HumanInputComponent"]
    assert [g["name"] for g in gates] == ["production_approval"]
