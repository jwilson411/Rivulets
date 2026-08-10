"""Pure logic tests for #101's capability scoring and election tie-break --
no network, no DB, no trio. See test_sync.py for the real two/three-engine
election/failover integration tests and the /sync/coordinator HTTP tests."""

from pathlib import Path

from rivulets.sync.coordinator import compute_capability_score, outranks


def test_outranks_higher_score_wins() -> None:
    assert outranks("peer-a", 10.0, "peer-b", 5.0) is True
    assert outranks("peer-b", 5.0, "peer-a", 10.0) is False


def test_outranks_tie_breaks_by_peer_id() -> None:
    assert outranks("peer-b", 5.0, "peer-a", 5.0) is True
    assert outranks("peer-a", 5.0, "peer-b", 5.0) is False


def test_outranks_identical_candidate_never_outranks_itself() -> None:
    assert outranks("peer-a", 5.0, "peer-a", 5.0) is False


def test_compute_capability_score_is_positive_and_deterministic_shape(tmp_path: Path) -> None:
    score_a = compute_capability_score(tmp_path, uptime_seconds=0.0)
    score_b = compute_capability_score(tmp_path, uptime_seconds=0.0)
    assert score_a > 0.0
    # Not asserting exact equality -- load average can legitimately drift
    # between two calls -- just that two near-simultaneous calls on the
    # same machine land in the same ballpark rather than wildly diverging.
    assert abs(score_a - score_b) < score_a * 0.5 + 1.0


def test_compute_capability_score_rewards_uptime(tmp_path: Path) -> None:
    fresh = compute_capability_score(tmp_path, uptime_seconds=0.0)
    seasoned = compute_capability_score(tmp_path, uptime_seconds=10_000.0)
    assert seasoned > fresh


def test_compute_capability_score_uptime_saturates(tmp_path: Path) -> None:
    at_saturation = compute_capability_score(tmp_path, uptime_seconds=600.0)
    way_past = compute_capability_score(tmp_path, uptime_seconds=1_000_000.0)
    assert at_saturation == way_past
