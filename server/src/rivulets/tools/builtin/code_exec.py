"""Code Execution built-in tool (FR-8.1, NFR-3.5, ADR-008).

Deliberately unimplemented: running arbitrary agent-generated Python is a
remote-code-execution vector, and ADR-008 requires platform-native
sandboxing (firejail on Linux, sandbox-exec on macOS, job objects on
Windows) before this tool may run anything. Wiring up a sandbox is a
dedicated piece of work, not a byproduct of scaffolding — see ADR-008 for
the chosen approach per platform.
"""

from agno.tools import tool


@tool
def execute_python(code: str) -> str:
    """Execute Python in a sandboxed environment. Not yet implemented —
    see docs/architecture/adrs.md ADR-008 before wiring this up."""
    raise NotImplementedError(
        "Code execution requires sandbox integration (ADR-008) — not yet wired up."
    )
