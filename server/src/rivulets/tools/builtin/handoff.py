"""Handoff tool (FR-6.1) — available to every agent, not opt-in like the
other built-ins (agentos/service.py attaches it unconditionally when
constructing each AgnoAgent).

This function is deliberately a pure LLM-facing tool with no side
effects of its own: it can't reach a DB session or the current rivulet_id,
so it just returns a confirmation string for the calling agent's own
conversation to continue from. The actual handoff — posting the visible
message (FR-6.3) and invoking the target agent with the handoff context
(FR-6.2) — happens in dispatch/service.py, which inspects the completed
run's tool calls for a "handoff" entry after the fact. That's where
rivulet/DB context naturally already lives (same as how guard state and
SSE events are handled), so it's simpler to detect-and-act there than to
smuggle request-scoped state into a tool function via a contextvar.
"""

from agno.tools import tool


@tool
def handoff(target_agent_name: str, context: str, urgency: str = "normal") -> str:
    """Hand off this conversation to another agent on the team by name,
    with context for what they should do. Assistant uses this to pick
    exactly one specialist. Specialists use it when a teammate should
    take the next step. Do not rely on keyword rematch."""
    return f"Handed off to {target_agent_name}: {context}"
