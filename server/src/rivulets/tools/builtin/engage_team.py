"""Mark that Assistant is ready to pick a specialist.

Same side-effect-free stub as handoff: the tool only returns a
confirmation string. This does not let keyword rematch wake the whole
roster. Prefer `handoff` to the teammate who should act, or
`hire_teammate` after the human agrees a missing role should be hired.
"""

from agno.tools import tool


@tool
def engage_team(reason: str) -> str:
    """Record that you are done gathering context. This does not let
    every specialist speak — hand off to the one teammate who should
    act, or hire a missing role after the human agrees.
    """
    return f"Team engaged: {reason}"
