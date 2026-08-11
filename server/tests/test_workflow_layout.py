"""#194: the pure auto-layout function (workflows/layout.py) that computes
fallback canvas coordinates for nodes without a saved position — exercised
directly against plain node ids / edge tuples, no DB/HTTP needed, mirroring
how test_workflow_engine.py favors direct-construction over API round-trips
for engine-internal logic."""

from rivulets.workflows.layout import X_SPACING, Y_SPACING, auto_layout


def test_linear_chain_places_nodes_in_increasing_columns() -> None:
    edges = [(None, "a"), ("a", "b"), ("b", "c")]
    positions = auto_layout(["a", "b", "c"], edges)
    assert positions["a"] == (0.0, 0.0)
    assert positions["b"] == (X_SPACING, 0.0)
    assert positions["c"] == (2 * X_SPACING, 0.0)


def test_branching_fan_out_stacks_siblings_in_same_column() -> None:
    edges = [(None, "entry"), ("entry", "left"), ("entry", "right")]
    positions = auto_layout(["entry", "left", "right"], edges)
    assert positions["entry"] == (0.0, 0.0)
    assert positions["left"][0] == positions["right"][0] == X_SPACING
    assert {positions["left"][1], positions["right"][1]} == {0.0, Y_SPACING}


def test_loop_back_edge_does_not_infinite_loop_or_revisit() -> None:
    edges = [(None, "a"), ("a", "b"), ("b", "a")]  # loop back, #199-style
    positions = auto_layout(["a", "b"], edges)
    assert positions["a"] == (0.0, 0.0)
    assert positions["b"] == (X_SPACING, 0.0)


def test_node_unreachable_from_entry_gets_trailing_column() -> None:
    edges = [(None, "entry"), ("entry", "reachable")]
    positions = auto_layout(["entry", "reachable", "orphan"], edges)
    assert positions["orphan"][0] == 2 * X_SPACING


def test_workflow_with_no_entry_edge_still_positions_every_node() -> None:
    positions = auto_layout(["a", "b"], [])
    assert set(positions) == {"a", "b"}
    assert positions["a"][0] == positions["b"][0] == 0.0
    assert {positions["a"][1], positions["b"][1]} == {0.0, Y_SPACING}
