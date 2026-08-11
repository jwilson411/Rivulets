"""Auto-layout fallback for the workflow canvas (#194, part of #120).

Computes x/y coordinates for nodes that don't have one yet -- either
saved before `WorkflowNode.position_x`/`position_y` existed, or created
without an explicit position. Not wired into any canvas UI here (that's
#195+); this is the pure layout function the API's node-listing fallback
(api/workflows.py) calls, and later the canvas itself can call directly.

Takes plain node ids and (from_id, to_id) edge tuples rather than ORM
rows or a particular Pydantic shape -- callers already have either
`WorkflowNode`/`WorkflowConnection` rows or `WorkflowNodeOut` DTOs handy,
and reducing to primitives here avoids coupling this module to either
representation (or to a Protocol that SQLAlchemy's `Mapped[...]`
descriptors don't structurally satisfy).

Layered/DAG layout: nodes are placed in columns by BFS distance (in
hops) from the workflow's entry node (the edge with `from_id=None`, same
convention as engine.py's `_entry_node_id`), ordered top-to-bottom
within a column by BFS discovery order. Cycles (loops, #199) don't cause
infinite recursion or re-visits -- a node's depth is fixed the first
time it's reached, matching the engine's own "first visit wins"
traversal shape. Nodes unreachable from the entry point (orphaned, or a
workflow with no entry edge yet) are appended as their own trailing
column so they still get a deterministic position instead of stacking
at the origin.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence

X_SPACING = 240.0
Y_SPACING = 140.0


def auto_layout(
    node_ids: Sequence[str], edges: Sequence[tuple[str | None, str]]
) -> dict[str, tuple[float, float]]:
    """Returns {node_id: (x, y)} for every id in `node_ids`.

    `edges` is (from_id, to_id) pairs; from_id=None marks the entry edge,
    same as `WorkflowConnection.from_node_id`."""
    node_id_set = set(node_ids)

    outbound: dict[str, list[str]] = defaultdict(list)
    entry_id: str | None = None
    for from_id, to_id in edges:
        if from_id is None:
            entry_id = to_id
            continue
        outbound[from_id].append(to_id)

    depth: dict[str, int] = {}
    order: list[str] = []
    if entry_id is not None and entry_id in node_id_set:
        depth[entry_id] = 0
        order.append(entry_id)
        queue = deque([entry_id])
        while queue:
            current = queue.popleft()
            for child in outbound.get(current, []):
                if child in depth or child not in node_id_set:
                    continue
                depth[child] = depth[current] + 1
                order.append(child)
                queue.append(child)

    unreached = [node_id for node_id in node_ids if node_id not in depth]
    if unreached:
        trailing_depth = (max(depth.values()) + 1) if depth else 0
        for node_id in unreached:
            depth[node_id] = trailing_depth
            order.append(node_id)

    columns: dict[int, list[str]] = defaultdict(list)
    for node_id in order:
        columns[depth[node_id]].append(node_id)

    return {
        node_id: (col * X_SPACING, row * Y_SPACING)
        for col, ids_in_col in columns.items()
        for row, node_id in enumerate(ids_in_col)
    }
