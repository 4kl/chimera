from chimera.state.graph import StateGraph
from chimera.state.models import StateTransition
from chimera.state.planner import StatePlanner


def _t(frm, to, role="go", action="tap", success=5, failure=0):
    return StateTransition(from_state=frm, to_state=to, role=role,
                           action=action, app_package="com.x",
                           success=success, failure=failure)


def test_plan_trivial_same_state_returns_empty():
    g = StateGraph("com.x", "1", [])
    assert StatePlanner(g).plan("A", "A") == []


def test_plan_single_hop():
    g = StateGraph("com.x", "1", [_t("A", "B", role="tap_b")])
    path = StatePlanner(g).plan("A", "B")
    assert path is not None
    assert len(path) == 1
    assert path[0].from_state == "A" and path[0].to_state == "B"
    assert path[0].role == "tap_b"


def test_plan_multi_hop_shortest():
    g = StateGraph("com.x", "1", [
        _t("A", "B", role="ab"),
        _t("B", "C", role="bc"),
        _t("A", "C", role="ac_slow", success=1, failure=10),  # low-conf
    ])
    path = StatePlanner(g).plan("A", "C")
    # Even though A→C is one hop, its low confidence should make A→B→C cheaper.
    assert [e.role for e in path] == ["ab", "bc"]


def test_plan_returns_none_when_unreachable():
    g = StateGraph("com.x", "1", [_t("A", "B"), _t("C", "D")])
    assert StatePlanner(g).plan("A", "D") is None


def test_plan_prefers_high_confidence_direct_over_long_but_perfect():
    g = StateGraph("com.x", "1", [
        _t("A", "Z", role="direct", success=20, failure=0),  # ~0.95 conf
        _t("A", "M", role="mid", success=20, failure=0),
        _t("M", "Z", role="to_z", success=20, failure=0),
    ])
    path = StatePlanner(g).plan("A", "Z")
    assert [e.role for e in path] == ["direct"]


def test_plan_penalizes_back_actions():
    g = StateGraph("com.x", "1", [
        _t("A", "B", role="back", action="back", success=20, failure=0),
        _t("A", "M", role="tap_m", action="tap", success=20, failure=0),
        _t("M", "B", role="tap_b", action="tap", success=20, failure=0),
    ])
    path = StatePlanner(g).plan("A", "B")
    # Both routes cost ~1 each, but the direct 'back' hop has a small penalty.
    # Single-hop back: cost ~= 1.0 + 0.25 = 1.25
    # Two-hop forward: cost ~= 1.0 * 2 = 2.0. So back still wins for a 1-hop case.
    # This test just verifies back isn't *preferred* over an equal-length forward:
    # swap to a case where forward has a 1-hop alternative of equal confidence.
    # (Redo) — check an A→B with BOTH options being single-hop.
    g2 = StateGraph("com.x", "1", [
        _t("A", "B", role="back_btn", action="back", success=20, failure=0),
        _t("A", "B", role="forward",  action="tap",  success=20, failure=0),
    ])
    chosen = StatePlanner(g2).plan("A", "B")[0]
    assert chosen.action == "tap"
