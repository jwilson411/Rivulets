"""Mark that Assistant is ready to pick a specialist — and route.

Same side-effect-free stub as handoff: the tool only returns a
confirmation string; the real work happens in dispatch/service.py's
_handle_engage_team, which inspects the completed run for this call.
Since the dispatcher stabilization (docs/dispatcher/), that handler does
more than post a marker: it runs a specialist-only routing pass over the
human's request (deterministic rules, then the LLM fallback — never the
orchestrator, never mention-only agents) and invokes the single best
match through the shared handoff pipeline. It still does not open keyword
rematch for the whole roster. Prefer `handoff` when you already know who
should act — this tool is for "someone should take this, route it".
"""

from agno.tools import tool


@tool
def engage_team(reason: str) -> str:
    """Record that you are done gathering context and the team should
    act. The best-matching specialist is selected and invoked for you.
    If you already know exactly who should take the work, call handoff
    with their name instead. This never wakes every specialist at once.
    """
    return f"Team engaged: {reason}"
