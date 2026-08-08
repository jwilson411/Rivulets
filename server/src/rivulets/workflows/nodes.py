"""Built-in workflow node executors (#24).

`node_type == 'agent'` reuses `agentos.run_agent` directly — an agent
node is not a distinct execution path, just this engine's caller of the
same entry point dispatch/service.py uses. The remaining types are plain
functions, not agno `Function`/LLM tools: they're invoked by the workflow
engine itself (workflows/engine.py), never by an agent's own tool-calling
loop, so there's no need to route them through the LLM-facing tool
machinery in tools/builtin/ (see tool_resolution.py's docstring for that
distinction).

Each executor takes the previous node's output as plain text and returns
plain text — "a node's output becomes the next node's input, unmodified"
(issue #24) applies uniformly whether that next node is an agent or
another utility node.
"""

import json

from agno.agent import Agent as AgnoAgent
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos import run_agent
from rivulets.agentos.models import resolve_model
from rivulets.db.models import Agent, WorkflowNode

NODE_TYPES = ("agent", "summarize", "transform", "conditional", "merge")

_SUMMARIZE_INSTRUCTIONS = (
    "Summarize the given text concisely, preserving the key points. "
    "Respond with the summary only, no preamble."
)


class ConditionNotMetError(Exception):
    """Raised by the 'conditional' node when its predicate doesn't match
    the input — signals the engine to end the run early (not a failure;
    see workflows/engine.py's handling)."""


class NoProviderConfiguredError(Exception):
    """Raised by 'summarize' when no LLM provider is available to call."""


def _load_config(node: WorkflowNode) -> dict[str, object]:
    return json.loads(node.config_json) if node.config_json else {}


async def execute_agent_node(
    db: AsyncSession, node: WorkflowNode, session_id: str, input_content: str
) -> str:
    if node.agent_id is None:
        raise ValueError(f"Node {node.name!r} has no agent assigned")
    agent = await db.get(Agent, node.agent_id)
    if agent is None:
        raise ValueError(f"Node {node.name!r} references a deleted agent")
    run_output = await run_agent(
        db, agent.id, input_content, session_id=session_id, user_id="workflow"
    )
    return run_output.get_content_as_string() or ""  # pyright: ignore[reportUnknownMemberType]


def execute_transform_node(node: WorkflowNode, input_content: str) -> str:
    """config: {"template": "..."} — "{input}" is replaced verbatim (a
    plain string substitution, not str.format, so template text containing
    other brace characters can't raise or be misinterpreted). An absent or
    empty template passes the input through unchanged."""
    config = _load_config(node)
    template = config.get("template")
    if not isinstance(template, str) or not template:
        return input_content
    return template.replace("{input}", input_content)


async def execute_summarize_node(db: AsyncSession, node: WorkflowNode, input_content: str) -> str:
    """No dedicated Agent DB row needed — reuses the same "pick a cheap
    dispatcher-tier model" policy as dispatch/llm_fallback.py and
    rule_generation.py for this kind of ad-hoc, non-agent LLM call.
    Imported lazily: dispatch/service.py also reaches into this package
    (dispatch/service.py's _handle_run_workflow_trigger, #24) to run the
    run_workflow tool, so a module-level import of a dispatch submodule
    here would risk a circular import depending on which package happens
    to load first at app startup."""
    from rivulets.dispatch.rule_generation import pick_dispatcher_model

    provider_model = await pick_dispatcher_model(db)
    if provider_model is None:
        raise NoProviderConfiguredError("No provider configured for the summarize node")
    model = await resolve_model(db, provider_model)
    summarizer = AgnoAgent(model=model, instructions=_SUMMARIZE_INSTRUCTIONS)
    run_output = await summarizer.arun(  # pyright: ignore[reportUnknownMemberType]
        input_content, stream=False
    )
    content = run_output.content
    return content if isinstance(content, str) else str(content)


def execute_conditional_node(node: WorkflowNode, input_content: str) -> str:
    """config: {"contains": "keyword"} — case-insensitive substring check
    against the input. Raises ConditionNotMetError to end the run early
    when the predicate fails; an absent/empty predicate always passes
    (a conditional node with no condition configured is a no-op, not a
    silent stop)."""
    config = _load_config(node)
    needle = config.get("contains")
    if not isinstance(needle, str) or not needle:
        return input_content
    if needle.lower() not in input_content.lower():
        raise ConditionNotMetError(f"Input did not contain {needle!r}")
    return input_content


def execute_merge_node(input_content: str) -> str:
    """Multi-input merge needs parallel branches, out of scope for the
    linear-only MVP engine (workflows/engine.py) — this is a pass-through
    placeholder so the node type exists in the schema/API today and the
    real merge behavior can land later without a new node_type."""
    return input_content
