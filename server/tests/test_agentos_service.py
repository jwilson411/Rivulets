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
from agno.run.agent import RunCompletedEvent, RunContentEvent, RunErrorEvent
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
