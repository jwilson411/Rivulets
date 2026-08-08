"""run_workflow tool (#24): lets an agent kick off a saved workflow
programmatically, e.g. a human typing "@some-agent run this workflow"
should let that agent launch it, not just a human typing the slash
command directly.

Opt-in like the rest of tools/builtin/ (unlike `handoff`, not attached to
every agent unconditionally) — a workspace decides which agents are
trusted to trigger which workflows via the normal agent_tool assignment.

Same deliberately side-effect-free shape as handoff.py: this function has
no DB/rivulet access, so it just confirms the request back to the calling
agent's own conversation. The actual run — looking up the named workflow
and executing it (workflows/engine.py) — happens in dispatch/service.py,
which inspects the completed run's tool calls for a "run_workflow" entry
after the fact, the same way it already does for handoff.
"""

from agno.tools import tool


@tool
def run_workflow(workflow_name: str, workflow_input: str) -> str:
    """Run a saved workflow by name, passing it the given input text. Use
    this when the conversation calls for a specific, predefined multi-step
    process (e.g. "run the release-checklist workflow") rather than
    something you should handle yourself."""
    return f"Running workflow {workflow_name!r} with input: {workflow_input}"
