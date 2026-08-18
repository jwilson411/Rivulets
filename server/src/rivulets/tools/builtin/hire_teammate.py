"""Hire a missing specialist onto this channel's team.

Same side-effect-free stub as handoff: the tool returns a confirmation
string. dispatch/service.py inspects the completed run, creates or
adopts the agent, adds them to the channel team, and then a same-turn
handoff can invoke them. Attached only to Assistant — specialists do
not hire.
"""

from agno.tools import tool


@tool
def hire_teammate(name: str, role: str, instructions: str, assignment: str) -> str:
    """Hire a specialist onto this channel's team after the human agrees.

    Use this only when a needed role is not already on the team and the
    human has said yes. `name` is the agent's display name (for example
    "DBA"). `role` is a short description of what they do (at least 10
    characters). `instructions` is their system prompt. `assignment` is
    the work they should take once hired — call handoff to this same
    name in the same turn so they actually start.
    """
    return f"Hired {name}: {assignment}"
