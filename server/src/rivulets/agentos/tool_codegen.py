"""Simple-mode custom tool codegen (FR-8.3 / US-031, #517): turn a
plain-language description into Agno SDK tool source for the user to
review. Generation only — nothing here touches Tool/ToolVersion rows or
tools_dir; the approve step is the existing advanced-mode create +
save_tool_version plumbing (api/tools.py), so generated code never
becomes runnable without passing through the same review/save gate a
hand-written tool does.

Follows dispatch/rule_generation.py's / evals/judge.py's LLM-call idiom:
`_run_codegen` is the monkeypatchable seam, budget caps are enforced
first (workspace scope — this call isn't attributable to any agent), and
spend is recorded via record_agent_run (source='tool_codegen'). Unlike
the judge's never-raise contract, failures here raise typed errors: the
API layer must answer the HTTP request with a plain-language next step
(the whole point of #517 is that a non-coder never sees a raw 501), and
"which plain language" depends on *why* it failed.
"""

import ast
import logging

from agno.agent import Agent as AgnoAgent
from agno.models.base import Model
from agno.run.agent import RunOutput
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.accounting import record_agent_run
from rivulets.agentos.models import resolve_model, resolve_tier_model

logger = logging.getLogger(__name__)


class ToolCodegenUnavailableError(Exception):
    """No model to generate with — a configuration gap, not a transient
    failure, so the plain-language next step is 'configure a provider or
    paste code', not 'retry'."""


class ToolCodegenFailedError(Exception):
    """The model call failed or produced unusable code — transient, so
    the plain-language next step is 'try again or ask an agent'."""


_INSTRUCTIONS = """You write a single-file Python custom tool for the Agno SDK.

The file you produce is loaded as a standalone module and the decorated function is
handed to an agent verbatim, so it must:
- `from agno.tools import tool` and decorate the function with `@tool`.
- Define exactly one top-level function whose name matches the requested tool name
  exactly — the loader looks the function up by that name and silently skips the
  tool otherwise.
- Give the function a docstring describing what it does and what its parameters
  mean; agents read it to decide when and how to call the tool.
- Type-hint every parameter and the return value, and return a string (or something
  trivially stringifiable) describing the result.
- Use only the Python standard library, except `httpx` for HTTP calls (it is
  installed; `requests` is not).
- Handle predictable failures (network errors, missing files, bad input) by
  returning a short error message string instead of raising.
- Contain no top-level side effects: no code outside the function besides imports.
- Never hardcode placeholder secrets; if the task needs a credential, take it as a
  function parameter.

The user reviews this code before it is registered. Output the complete file
contents in `source_code` — no markdown fences, no commentary."""


class GeneratedToolCode(BaseModel):
    source_code: str = Field(description="The complete Python source file contents.")


def _build_prompt(name: str, description: str, prompt: str) -> str:
    return (
        f"Tool name (the function must be named exactly this): {name}\n"
        f"Description agents will see: {description}\n"
        f"What the user wants it to do:\n{prompt}"
    )


async def _run_codegen(model: Model, prompt: str) -> RunOutput:
    """Isolated seam for tests to monkeypatch — same idiom as
    rule_generation.py's `_run_generator` / judge.py's
    `_run_judge_generator`. Returns the raw RunOutput (not just the
    parsed code) so the caller can record token/cost accounting."""
    generator = AgnoAgent(model=model, instructions=_INSTRUCTIONS)
    # arun()'s overloads carry Unknown type args from agno's own generics —
    # same benign gap noted in agentos/service.py's run_agent().
    return await generator.arun(  # pyright: ignore[reportUnknownMemberType]
        prompt, output_schema=GeneratedToolCode, stream=False
    )


def _validate_generated_source(source_code: str, name: str) -> None:
    """Reject code the save/load pipeline would only fail on later, and
    fail on *worse* terms: save_tool_version's compile() check would bounce
    a syntax error back at a non-coder as 'Invalid Python', and a wrong
    function name wouldn't fail at all — _load_custom_tool
    (tool_resolution.py) just silently skips the tool at agent build time,
    the exact 'silently unusable' trap api/tools.py's docstring warns
    about. Better for generation itself to fail retryably."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise ToolCodegenFailedError(f"generated code is not valid Python: {exc}") from exc
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return
    raise ToolCodegenFailedError(
        f"generated code does not define a top-level function named {name!r}"
    )


async def generate_tool_code(db: AsyncSession, name: str, description: str, prompt: str) -> str:
    """Generate Agno tool source for review. Raises
    ToolCodegenUnavailableError when there's no model to call (no provider
    configured, or a tripped hard-stop budget cap — both 'not right now on
    this machine' conditions, not retry-worthy ones) and
    ToolCodegenFailedError when the call itself fails or returns unusable
    code.

    Lazy import of dispatch.budgets: rivulets.dispatch's package init
    pulls in rivulets.api, whose init imports this package back — same
    circular-import hazard evals/judge.py's judge_llm notes."""
    from rivulets.dispatch.budgets import BudgetCapBlockedError, enforce_budget_caps

    try:
        await enforce_budget_caps(db, None, None)
    except BudgetCapBlockedError as exc:
        raise ToolCodegenUnavailableError(str(exc)) from exc

    # Capable tier, not the cheap/dispatcher one: this is a one-shot call
    # whose output a non-coder must be able to trust largely as-is. Cheap
    # is only a fallback for providers with no capable default.
    provider_model = await resolve_tier_model(db, "capable") or await resolve_tier_model(
        db, "cheap"
    )
    if provider_model is None:
        raise ToolCodegenUnavailableError("no model provider is configured")

    try:
        model = await resolve_model(db, provider_model)
        run_output = await _run_codegen(model, _build_prompt(name, description, prompt))
    except Exception as exc:
        logger.warning("Tool codegen failed for %r", name, exc_info=True)
        raise ToolCodegenFailedError(str(exc)) from exc

    # Recorded before the content is parsed — the tokens were spent
    # whether or not the model produced usable structured output, same
    # ordering as llm_fallback.py's / judge.py's recording.
    await record_agent_run(
        db, None, provider_model, None, "completed", run_output, source="tool_codegen"
    )

    content = run_output.content
    generated = content if isinstance(content, GeneratedToolCode) else None
    if generated is None or not generated.source_code.strip():
        raise ToolCodegenFailedError("model returned no code")

    _validate_generated_source(generated.source_code, name)
    return generated.source_code
