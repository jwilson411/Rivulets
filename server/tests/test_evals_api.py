"""#95: EvalSuite/EvalCase CRUD + suite execution (api/evals.py) --
HTTP-level coverage, mirroring test_workflow_schedules_api.py's CRUD style.
The one true end-to-end test monkeypatches `rivulets.evals.runner.run_agent`
(same seam test_rivulet_dispatch.py uses for dispatch/service.py) so it
needs no real LLM call, but still exercises the full HTTP -> runner ->
judge -> DB round-trip for real.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus
from fastapi.testclient import TestClient


def _create_agent(client: TestClient, headers: dict[str, str], name: str = "Grader") -> str:
    created = client.post(
        "/api/v1/agents",
        json={
            "name": name,
            "description": "A test agent",
            "instructions": "Be helpful.",
            "model": "anthropic:claude-haiku-4-5-20251001",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent_id: str = created.json()["id"]
    return agent_id


def _create_workflow(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/api/v1/workflows", json={"name": name, "description": "test workflow"}, headers=headers
    )
    assert created.status_code == 201, created.text
    workflow_id: str = created.json()["id"]
    return workflow_id


def _create_suite(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    *,
    agent_id: str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if agent_id is not None:
        body["agent_id"] = agent_id
    if workflow_id is not None:
        body["workflow_id"] = workflow_id
    resp = client.post("/api/v1/evals/suites", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_agent_suite_and_get_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "greeting-suite", agent_id=agent_id)

    assert suite["agent_id"] == agent_id
    assert suite["workflow_id"] is None
    assert suite["subject_type"] == "agent"
    assert suite["case_count"] == 0

    fetched = client.get(f"/api/v1/evals/suites/{suite['id']}", headers=auth_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == suite["id"]


def test_create_workflow_suite(client: TestClient, auth_headers: dict[str, str]) -> None:
    workflow_id = _create_workflow(client, auth_headers, "digest-flow")
    suite = _create_suite(client, auth_headers, "digest-suite", workflow_id=workflow_id)
    assert suite["subject_type"] == "workflow"
    assert suite["workflow_id"] == workflow_id


def test_create_suite_rejects_neither_subject(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post("/api/v1/evals/suites", json={"name": "no-subject"}, headers=auth_headers)
    assert resp.status_code == 422


def test_create_suite_rejects_both_subjects(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    workflow_id = _create_workflow(client, auth_headers, "both-flow")
    resp = client.post(
        "/api/v1/evals/suites",
        json={"name": "both", "agent_id": agent_id, "workflow_id": workflow_id},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_suite_rejects_unknown_agent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/evals/suites",
        json={"name": "ghost", "agent_id": "does-not-exist"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_list_suites_spans_both_subject_types(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers, "AgentX")
    workflow_id = _create_workflow(client, auth_headers, "flow-x")
    _create_suite(client, auth_headers, "agent-suite", agent_id=agent_id)
    _create_suite(client, auth_headers, "workflow-suite", workflow_id=workflow_id)

    listed = client.get("/api/v1/evals/suites", headers=auth_headers).json()
    names = {s["name"] for s in listed}
    assert {"agent-suite", "workflow-suite"} <= names


def test_update_suite_renames(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "old-name", agent_id=agent_id)
    resp = client.patch(
        f"/api/v1/evals/suites/{suite['id']}", json={"name": "new-name"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "new-name"


def test_delete_suite(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "deletable", agent_id=agent_id)
    resp = client.delete(f"/api/v1/evals/suites/{suite['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert (
        client.get(f"/api/v1/evals/suites/{suite['id']}", headers=auth_headers).status_code == 404
    )


def test_create_exact_case_requires_expected_output(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "case-suite", agent_id=agent_id)
    resp = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={"name": "c1", "input_content": "hi", "judge_type": "exact"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_llm_judge_case_requires_rubric(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "case-suite-2", agent_id=agent_id)
    resp = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={"name": "c1", "input_content": "hi", "judge_type": "llm_judge"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_structural_case_on_agent_suite_succeeds(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "structural-suite", agent_id=agent_id)
    resp = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={
            "name": "calls-search",
            "input_content": "find cats",
            "judge_type": "structural",
            "expected_tool_name": "search",
            "expected_tool_args": {"query": "cats"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expected_tool_args"] == {"query": "cats"}


def test_create_structural_case_on_workflow_suite_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    workflow_id = _create_workflow(client, auth_headers, "structural-flow")
    suite = _create_suite(
        client, auth_headers, "structural-workflow-suite", workflow_id=workflow_id
    )
    resp = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={
            "name": "calls-search",
            "input_content": "find cats",
            "judge_type": "structural",
            "expected_tool_name": "search",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_list_and_delete_case(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "list-delete-suite", agent_id=agent_id)
    created = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={"name": "c1", "input_content": "hi", "judge_type": "exact", "expected_output": "hi"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    listed = client.get(f"/api/v1/evals/suites/{suite['id']}/cases", headers=auth_headers).json()
    assert [c["id"] for c in listed] == [case_id]

    deleted = client.delete(
        f"/api/v1/evals/suites/{suite['id']}/cases/{case_id}", headers=auth_headers
    )
    assert deleted.status_code == 204
    listed_after = client.get(
        f"/api/v1/evals/suites/{suite['id']}/cases", headers=auth_headers
    ).json()
    assert listed_after == []


def test_run_suite_with_no_cases_returns_400(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "empty-suite", agent_id=agent_id)
    resp = client.post(f"/api/v1/evals/suites/{suite['id']}/run", headers=auth_headers)
    assert resp.status_code == 400


def test_run_agent_suite_end_to_end(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full HTTP -> runner -> judge -> DB round-trip: only the actual agent
    invocation (`rivulets.evals.runner.run_agent`) is mocked, so this needs
    no real LLM call while still exercising every other layer for real."""

    async def fake_run_agent(*_args: object, **_kwargs: object) -> Any:
        return SimpleNamespace(
            status=RunStatus.completed, tools=None, get_content_as_string=lambda: "hello there"
        )

    monkeypatch.setattr("rivulets.evals.runner.run_agent", fake_run_agent)

    agent_id = _create_agent(client, auth_headers)
    suite = _create_suite(client, auth_headers, "e2e-suite", agent_id=agent_id)
    created_case = client.post(
        f"/api/v1/evals/suites/{suite['id']}/cases",
        json={
            "name": "greets",
            "input_content": "hi",
            "judge_type": "substring",
            "expected_output": "hello",
        },
        headers=auth_headers,
    )
    assert created_case.status_code == 201, created_case.text

    run_resp = client.post(f"/api/v1/evals/suites/{suite['id']}/run", headers=auth_headers)
    assert run_resp.status_code == 200, run_resp.text
    run = run_resp.json()
    assert run["status"] == "completed"
    assert run["case_count"] == 1
    assert run["pass_count"] == 1

    runs_listed = client.get(
        f"/api/v1/evals/suites/{suite['id']}/runs", headers=auth_headers
    ).json()
    assert [r["id"] for r in runs_listed] == [run["id"]]

    results = client.get(
        f"/api/v1/evals/suites/{suite['id']}/runs/{run['id']}/results", headers=auth_headers
    ).json()
    assert len(results) == 1
    assert results[0]["status"] == "passed"
    assert results[0]["actual_output"] == "hello there"


def test_deleting_agent_cascades_suite(client: TestClient, auth_headers: dict[str, str]) -> None:
    agent_id = _create_agent(client, auth_headers, "Deletable")
    suite = _create_suite(client, auth_headers, "cascade-suite", agent_id=agent_id)

    resp = client.delete(f"/api/v1/agents/{agent_id}", headers=auth_headers)
    assert resp.status_code == 204, resp.text
    assert (
        client.get(f"/api/v1/evals/suites/{suite['id']}", headers=auth_headers).status_code == 404
    )
