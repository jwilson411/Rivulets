"""Unlock the rest of the channel team (orchestrator lock).

Same side-effect-free stub shape as handoff: the tool only returns a
confirmation string. dispatch/service.py inspects the completed run for
an `engage_team` call and posts the visible `team_engaged` message that
actually flips the lock.
"""

from agno.tools import tool


@tool
def engage_team(reason: str) -> str:
    """Unlock the rest of the team so specialists can join this conversation.

    Use this only when the human's request is clear enough that another
    teammate should act — not while you are still asking a clarifying
    question. After this call, keyword and always rules on the rest of
    the roster apply again.
    """
    return f"Team engaged: {reason}"
