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


async def test_sync_agents_registers_without_a_model_when_keys_are_locked(
    db_session: AsyncSession,
) -> None:
    """#404: Docker startup calls sync_agents before login unlocks the
    credential store. Skipping those agents left dispatch matching a DB
    row that run_agent then refused. They must still land in the
    registry (model=None) so a later invoke can resolve the key."""
    from rivulets.agentos.models import AUTO_MODEL
    from rivulets.agentos.service import get_agentos, sync_agents
    from rivulets.db.models import Agent
    from rivulets.security.session import get_session_key_store

    db_session.add(
        Agent(
            id="agent-locked",
            name="Assistant",
            description="starter",
            instructions="Be helpful.",
            model=AUTO_MODEL,
        )
    )
    await db_session.commit()
    get_session_key_store().clear()

    await sync_agents(db_session)

    registered = [a for a in (get_agentos().agents or []) if a.id == "agent-locked"]
    assert len(registered) == 1
    assert registered[0].model is None  # pyright: ignore[reportAttributeAccessIssue]


async def test_run_agent_resolves_model_when_registered_without_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent registered before login (model=None) must pick up a model
    on the first invoke instead of raising 'not registered'."""
    from agno.models.openai import OpenAIChat

    from rivulets.agentos.service import get_agentos
    from rivulets.db.models import Agent

    reset_agentos_for_testing()
    init_agentos()
    db_session.add(
        Agent(
            id="agent-1",
            name="Assistant",
            description="starter",
            instructions="Be helpful.",
            model="openai:gpt-4o-mini",
        )
    )
    await db_session.commit()
    placeholder = AgnoAgent(id="agent-1", name="Assistant")
    placeholder.arun = _scripted_arun(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        [RunCompletedEvent(content="Hello")]
    )
    get_agentos().agents = [placeholder]  # pyright: ignore[reportAttributeAccessIssue]

    async def fake_resolve(_db: AsyncSession, _row: Agent) -> OpenAIChat:
        return OpenAIChat(id="gpt-4o-mini", api_key="sk-test")

    monkeypatch.setattr("rivulets.agentos.service._resolve_agent_model", fake_resolve)

    original_deep_copy = placeholder.deep_copy

    def spying_deep_copy(*, update: Any = None) -> AgnoAgent:
        clone = original_deep_copy(update=update)
        clone.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
            [RunCompletedEvent(content="Hello")]
        )
        return clone

    placeholder.deep_copy = spying_deep_copy  # pyright: ignore[reportAttributeAccessIssue]

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")
    assert result.status is RunStatus.completed
    assert result.content == "Hello"
    reset_agentos_for_testing()


async def test_run_agent_resyncs_once_if_missing_from_registry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message after login (or after a provider is added) should not
    stay silent just because startup skipped registration."""
    reset_agentos_for_testing()
    init_agentos()
    agent = AgnoAgent(id="agent-1", name="Test Agent")
    agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [RunCompletedEvent(content="Hello")]
    )
    sync_calls = 0

    async def fake_sync(_db: AsyncSession) -> None:
        nonlocal sync_calls
        sync_calls += 1
        get_agentos().agents = [agent]  # pyright: ignore[reportAttributeAccessIssue]

    monkeypatch.setattr("rivulets.agentos.service.sync_agents", fake_sync)

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert sync_calls == 1
    assert result.status is RunStatus.completed
    assert result.content == "Hello"
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


async def test_run_agent_requests_lifecycle_events_from_arun(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """Regression test: agno resolves `stream_events` to False by default
    even when stream=True, and then the stream carries ONLY content
    deltas — no ToolCall* events (so handoff/engage_team/builtin-trigger
    detection and the #30 status indicators silently never fire) and no
    RunCompleted/RunError terminal event (so every provider fell through
    to the synthesized-completion path, with no tool calls and no
    metrics). run_agent must explicitly opt in. The other tests here
    script arun() directly and ignore its kwargs, so only this test can
    catch the flag going missing."""
    captured_kwargs: dict[str, object] = {}

    def capturing_arun(*_args: object, **kwargs: object):  # noqa: ANN202
        captured_kwargs.update(kwargs)

        async def gen() -> AsyncIterator[Any]:
            yield RunCompletedEvent(content="done")

        return gen()

    registered_agent.arun = capturing_arun  # pyright: ignore[reportAttributeAccessIssue]

    await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert captured_kwargs.get("stream") is True
    assert captured_kwargs.get("stream_events") is True


async def test_run_agent_collects_tool_calls_from_completion_events(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """Regression test: agno's RunCompletedEvent never carries the run's
    tool calls (create_run_completed_event doesn't copy `tools` off the
    RunOutput, in 2.8.6 and 2.8.7 alike), so reading `event.tools` alone
    made every after-the-run tool-call inspection in dispatch/service.py
    a silent no-op — an Assistant that called handoff() would *say* the
    specialist was on it while nobody was ever invoked. The executions
    must be collected from the ToolCallCompleted/Error events instead."""
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            ToolCallStartedEvent(tool=ToolExecution(tool_name="handoff")),
            ToolCallCompletedEvent(
                tool=ToolExecution(
                    tool_name="handoff",
                    tool_args={"target_agent_name": "Researcher", "context": "find rankings"},
                )
            ),
            ToolCallStartedEvent(tool=ToolExecution(tool_name="http_request")),
            ToolCallErrorEvent(tool=ToolExecution(tool_name="http_request"), error="boom"),
            RunCompletedEvent(content="done"),
        ]
    )

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.completed
    assert [t.tool_name for t in result.tools or []] == ["handoff", "http_request"]
    assert (result.tools or [])[0].tool_args == {
        "target_agent_name": "Researcher",
        "context": "find rankings",
    }


async def test_run_agent_synthesized_completion_carries_collected_tool_calls(
    db_session: AsyncSession, registered_agent: AgnoAgent
) -> None:
    """The no-terminal-event fallback (ollama-style streams) keeps any
    tool calls whose completion events did arrive, instead of dropping
    them with the missing RunCompletedEvent."""
    registered_agent.arun = _scripted_arun(  # pyright: ignore[reportAttributeAccessIssue]
        [
            RunContentEvent(content="working"),
            ToolCallCompletedEvent(tool=ToolExecution(tool_name="engage_team")),
        ]
    )

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1")

    assert result.status is RunStatus.completed
    assert [t.tool_name for t in result.tools or []] == ["engage_team"]


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

    result = await run_agent(db_session, "agent-1", "hi", session_id="s-1", on_token=tokens.append)

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
