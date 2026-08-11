"""agentos/service.py's run_agent -- specifically its event-loop-to-
RunOutput synthesis, which every other test in the suite bypasses by
monkeypatching run_agent itself (test_streaming.py, test_rivulet_dispatch.py,
etc). Drives a real AgnoAgent's arun() with a scripted async generator so
these exercise the actual stream-consuming loop, not a stand-in for it.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from agno.models.response import ToolExecution
from agno.run.agent import (
    RunCompletedEvent,
    RunContentEvent,
    RunErrorEvent,
    RunOutput,
    ToolCallCompletedEvent,
    ToolCallErrorEvent,
    ToolCallStartedEvent,
)
from agno.run.base import RunStatus
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.service import (
    get_agentos,
    init_agentos,
    reset_agentos_for_testing,
    run_agent,
)


@pytest.fixture
def registered_agent() -> Iterator[AgnoAgent]:
    reset_agentos_for_testing()
    init_agentos()
    agent = AgnoAgent(id="agent-1", name="Test Agent")
    get_agentos().agents = [agent]  # pyright: ignore[reportAttributeAccessIssue]
    yield agent
    reset_agentos_for_testing()


def _scripted_arun(events: list[Any]):  # noqa: ANN202
    async def arun(*_args: object, **_kwargs: object) -> AsyncIterator[Any]:
        for event in events:
            yield event

    return arun


async def test_run_agent_raises_when_unregistered(db_session: AsyncSession) -> None:
    reset_agentos_for_testing()
    init_agentos()
    with pytest.raises(ValueError, match="not registered"):
        await run_agent(db_session, "no-such-agent", "hi", session_id="s-1")
    reset_agentos_for_testing()


async def test_run_agent_returns_completed_output_on_terminal_event(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            RunContentEvent(content="Hel"),
            RunContentEvent(content="lo"),
            RunCompletedEvent(content="Hello"),
        ]
    )

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.completed
    assert result.content == "Hello"


async def test_run_agent_returns_error_output_on_error_event(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [RunErrorEvent(content="provider exploded")]
    )

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.error
    assert result.content == "provider exploded"


async def test_run_agent_calls_on_token_for_each_content_delta(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            RunContentEvent(content="Hel"),
            RunContentEvent(content="lo"),
            RunCompletedEvent(content="Hello"),
        ]
    )
    tokens: list[str] = []

    await run_agent(db_session, "agent-1", "hi", session_id="s-1", on_token=tokens.append)

    assert tokens == ["Hel", "lo"]


async def test_run_agent_synthesizes_completion_when_stream_ends_without_terminal_event(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """Regression test: observed in practice against a local OpenAI-
    compatible backend (ollama) -- the stream ends after its last
    RunContentEvent without ever emitting RunCompletedEvent, even though
    the model finished normally and produced real content. This must not
    be treated as a failure -- the reply is real and shouldn't be thrown
    away."""
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [RunContentEvent(content="par"), RunContentEvent(content="tial")]
    )

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.completed
    assert result.content == "partial"


async def test_run_agent_raises_when_stream_ends_with_no_content_and_no_terminal_event(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """Nothing streamed at all is a genuine failure, not a provider that
    merely skips the terminal event -- dispatch/service.py's
    except-and-skip around this call (NFR-2.4) depends on this raising."""
    registered_agent.arun = _scripted_arun([])  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(RuntimeError, match="ended without a completion or error event"):
        await run_agent(db_session, "agent-1", "hi", session_id="s-1")


async def test_run_agent_reports_executing_tool_status_for_a_regular_tool_call(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """#30: a non-handoff tool call reports ("executing_tool", tool_name),
    then reverts to ("thinking", None) once it completes -- never leaking
    tool_args (FR-5.5)."""
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            ToolCallStartedEvent(
                tool=ToolExecution(tool_name="web_search", tool_args={"query": "secret"})
            ),
            ToolCallCompletedEvent(tool=ToolExecution(tool_name="web_search")),
            RunCompletedEvent(content="done"),
        ]
    )
    statuses: list[tuple[str, str | None]] = []

    await run_agent(
        db_session,
        "agent-1",
        "hi",
        session_id="s-1",
        on_status=lambda status, detail: statuses.append((status, detail)),
    )

    assert statuses == [("executing_tool", "web_search"), ("thinking", None)]


async def test_run_agent_reports_waiting_for_handoff_status_for_the_handoff_tool(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """#30: the handoff tool (available to every agent) gets its own
    distinct status rather than the generic "executing_tool" label."""
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            ToolCallStartedEvent(tool=ToolExecution(tool_name="handoff")),
            RunCompletedEvent(content="done"),
        ]
    )
    statuses: list[tuple[str, str | None]] = []

    await run_agent(
        db_session,
        "agent-1",
        "hi",
        session_id="s-1",
        on_status=lambda status, detail: statuses.append((status, detail)),
    )

    assert statuses == [("waiting_for_handoff", None)]


async def test_run_agent_reports_thinking_status_after_a_failed_tool_call(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            ToolCallStartedEvent(tool=ToolExecution(tool_name="http_request")),
            ToolCallErrorEvent(tool=ToolExecution(tool_name="http_request"), error="boom"),
            RunCompletedEvent(content="done"),
        ]
    )
    statuses: list[tuple[str, str | None]] = []

    await run_agent(
        db_session,
        "agent-1",
        "hi",
        session_id="s-1",
        on_status=lambda status, detail: statuses.append((status, detail)),
    )

    assert statuses == [("executing_tool", "http_request"), ("thinking", None)]


async def test_run_agent_model_override_does_not_run_on_the_registered_instance(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """Auto mode (#23): a model_override must swap the model for that one
    call via a clone, not mutate the shared registered agent -- otherwise
    concurrent invocations of the same auto-mode agent would race on which
    model is set at call time."""
    registered_original_arun = _scripted_arun([RunCompletedEvent(content="never")])
    registered_agent.arun = registered_original_arun  # pyright: ignore[reportAttributeAccessIssue]
    override_model = Claude(id="claude-opus-5", api_key="sk-fake")

    called_on_override = False

    def cloned_arun(*_args: object, **_kwargs: object):  # noqa: ANN202
        nonlocal called_on_override
        called_on_override = True
        return _scripted_arun([RunCompletedEvent(content="Hello from override")])()

    original_deep_copy = registered_agent.deep_copy

    def spying_deep_copy(*, update: Any = None) -> AgnoAgent:
        clone = original_deep_copy(update=update)
        clone.arun = cloned_arun  # pyright: ignore[reportAttributeAccessIssue]
        return clone

    registered_agent.deep_copy = spying_deep_copy  # pyright: ignore[reportAttributeAccessIssue]

    result = await run_agent(
        db_session, "agent-1", "hi", session_id="s-1", model_override=override_model
    )

    assert called_on_override is True
    assert result.content == "Hello from override"
    # The registered singleton itself is untouched -- the clone got the
    # override, this fixture's own agent (created with no model) didn't.
    assert registered_agent.model is None


async def test_run_agent_calls_schema_constrained_agent_non_streamed(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """#107: an agent with output_schema set skips the streaming loop
    entirely -- stream=False, no on_token calls -- and returns whatever
    RunOutput agno hands back directly."""
    registered_agent.output_schema = {"type": "object"}
    calls: list[dict[str, Any]] = []

    async def structured_arun(*_args: object, **kwargs: object) -> RunOutput:
        calls.append(dict(kwargs))
        return RunOutput(content={"answer": "42"}, status=RunStatus.completed, content_type="dict")

    registered_agent.arun = structured_arun  # pyright: ignore[reportAttributeAccessIssue]
    tokens: list[str] = []

    result = await run_agent(
        db_session, "agent-1", "hi", session_id="s-1", on_token=tokens.append
    )

    assert result.status is RunStatus.completed
    assert result.content == {"answer": "42"}
    assert tokens == []
    assert calls == [{"stream": False, "session_id": "s-1", "user_id": "human"}]


async def test_run_agent_treats_schema_violation_as_error(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """#107 open question, resolved: a model that doesn't comply with the
    schema (agno leaves content as the raw string, content_type='str',
    without failing the run itself) is promoted to RunStatus.error here --
    a genuine agent failure, same as any other bad response."""
    registered_agent.output_schema = {"type": "object"}

    async def non_compliant_arun(*_args: object, **_kwargs: object) -> RunOutput:
        return RunOutput(content="not valid json", status=RunStatus.completed, content_type="str")

    registered_agent.arun = non_compliant_arun  # pyright: ignore[reportAttributeAccessIssue]

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.error
    assert result.content == "not valid json"
