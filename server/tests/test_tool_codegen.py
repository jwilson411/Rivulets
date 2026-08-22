"""Simple-mode custom tool codegen (FR-8.3, #517) — agentos/tool_codegen.py.

Follows test_evals_judge.py's LLM-call idiom exactly: monkeypatch the
private `_run_codegen` seam, never `arun` itself. Unlike the judge's
never-raise contract, generate_tool_code fails by raising typed errors —
the API layer (test_tools_api.py) owns turning those into plain-language
HTTP answers, so what's asserted here is *which* error, since that
decides which next step the user is offered.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.tool_codegen import (
    GeneratedToolCode,
    ToolCodegenFailedError,
    ToolCodegenUnavailableError,
    generate_tool_code,
)
from rivulets.db.models import AgentRun, BudgetCap, ProviderConfig

_VALID_SOURCE = '''
from agno.tools import tool


@tool
def greet(name: str) -> str:
    """Greets someone."""
    return f"Hello, {name}!"
'''


def _fake_run_output(content: object) -> Any:
    """RunOutput-like double for the `_run_codegen` seam --
    `.metrics`/`.tools` deliberately absent, same duck-typing
    record_agent_run already tolerates from other tests' doubles."""
    return SimpleNamespace(content=content)


async def _add_provider(db: AsyncSession) -> None:
    db.add(ProviderConfig(provider="anthropic", label="Anthropic", api_key_ref="ref-1"))
    await db.commit()


def _patch_resolve_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr("rivulets.agentos.tool_codegen.resolve_model", fake_resolve_model)


def _patch_codegen(monkeypatch: pytest.MonkeyPatch, content: object) -> None:
    async def fake_run_codegen(*_args: object, **_kwargs: object) -> Any:
        return _fake_run_output(content)

    monkeypatch.setattr("rivulets.agentos.tool_codegen._run_codegen", fake_run_codegen)


async def test_generate_is_unavailable_with_no_provider_configured(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(ToolCodegenUnavailableError):
        await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")


async def test_generate_fails_when_resolve_model_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_provider(db_session)

    async def fake_resolve_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no keychain in CI")

    monkeypatch.setattr("rivulets.agentos.tool_codegen.resolve_model", fake_resolve_model)

    with pytest.raises(ToolCodegenFailedError):
        await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")


async def test_generate_fails_when_output_is_unstructured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_provider(db_session)
    _patch_resolve_model(monkeypatch)
    _patch_codegen(monkeypatch, "plain text, not GeneratedToolCode")

    with pytest.raises(ToolCodegenFailedError):
        await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")


async def test_generate_fails_on_invalid_python(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_tool_version would bounce a syntax error back at a non-coder
    as 'Invalid Python' at approve time -- generation itself must fail
    (retryably) instead of handing over a broken draft."""
    await _add_provider(db_session)
    _patch_resolve_model(monkeypatch)
    _patch_codegen(monkeypatch, GeneratedToolCode(source_code="def greet(:\n    pass"))

    with pytest.raises(ToolCodegenFailedError):
        await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")


async def test_generate_fails_when_function_name_does_not_match(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_load_custom_tool looks the function up by the tool's name and
    silently skips the tool otherwise -- the 'silently unusable' trap
    api/tools.py's docstring warns about. A wrong name must fail
    generation, not surface weeks later as a tool that never resolves."""
    await _add_provider(db_session)
    _patch_resolve_model(monkeypatch)
    _patch_codegen(monkeypatch, GeneratedToolCode(source_code=_VALID_SOURCE))

    with pytest.raises(ToolCodegenFailedError):
        await generate_tool_code(db_session, "other_name", "Greets someone.", "say hello")


async def test_generate_is_blocked_by_a_tripped_workspace_hard_stop_cap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same fail-closed treatment as judge_llm/#354: codegen is billed
    spend, so a tripped workspace hard_stop refuses the call before the
    model is ever invoked. Unavailable (not Failed): retrying won't help
    until the cap resets, so the user should be steered to pasting code."""
    await _add_provider(db_session)
    db_session.add(
        BudgetCap(scope_type="workspace", period="day", limit_usd=0.5, action="hard_stop")
    )
    db_session.add(
        AgentRun(model="anthropic:claude-3-5-haiku-latest", status="completed", cost_usd=1.0)
    )
    await db_session.commit()

    calls: list[str] = []

    async def counting_codegen(*_args: object, **_kwargs: object) -> Any:
        calls.append("called")
        return _fake_run_output(GeneratedToolCode(source_code=_VALID_SOURCE))

    _patch_resolve_model(monkeypatch)
    monkeypatch.setattr("rivulets.agentos.tool_codegen._run_codegen", counting_codegen)

    with pytest.raises(ToolCodegenUnavailableError):
        await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")
    assert calls == []  # the model was never invoked


async def test_generate_returns_source_and_records_its_spend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _add_provider(db_session)
    _patch_resolve_model(monkeypatch)

    prompts: list[str] = []

    async def fake_run_codegen(_model: object, prompt: str) -> Any:
        prompts.append(prompt)
        return SimpleNamespace(
            content=GeneratedToolCode(source_code=_VALID_SOURCE),
            metrics=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    monkeypatch.setattr("rivulets.agentos.tool_codegen._run_codegen", fake_run_codegen)

    source = await generate_tool_code(db_session, "greet", "Greets someone.", "say hello")

    assert source == _VALID_SOURCE
    # The prompt carries all three user-supplied pieces.
    assert "greet" in prompts[0]
    assert "Greets someone." in prompts[0]
    assert "say hello" in prompts[0]

    run = (await db_session.scalars(select(AgentRun))).one()
    assert run.source == "tool_codegen"
    assert run.agent_id is None
    assert run.total_tokens == 150
    # Capable tier, not the cheap/dispatcher one -- the generated code has
    # to be trustworthy largely as-is for a non-coder.
    assert run.model == "anthropic:claude-opus-5"
