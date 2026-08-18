"""First-party credentialed integrations (#458).

Tokens live in the credential store. The in-process registry is how
sync `@tool` callables resolve a connected account without opening the
async SQLAlchemy session (the test suite's in-memory DB is not on
`db_path`).
"""

from rivulets.integrations.registry import (
    ConnectedAccount,
    get_connected_account,
    list_connected_accounts,
    load_integration_registry,
    reset_integration_registry_for_testing,
)

__all__ = [
    "ConnectedAccount",
    "get_connected_account",
    "list_connected_accounts",
    "load_integration_registry",
    "reset_integration_registry_for_testing",
]
