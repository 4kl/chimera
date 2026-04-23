import sqlite3

from chimera.memory.store import Memory
from chimera.state.models import StateFeature, StateTransition, UIState
from chimera.state.store import StateStore


def _store(tmp_path):
    m = Memory(str(tmp_path / "s.db"))
    return StateStore(m.connection)


def _sample_state(name="chat_screen", ver="2.24"):
    return UIState(
        name=name,
        app_package="com.whatsapp",
        app_version=ver,
        features=[StateFeature("resource_id", "com.whatsapp:id/entry"),
                  StateFeature("resource_id", "com.whatsapp:id/send")],
        fingerprints=["fp-1"],
        allowed_roles=["message_input", "send_button"],
        confidence=0.8,
    )


def test_state_upsert_and_get(tmp_path):
    s = _store(tmp_path)
    s.upsert_state(_sample_state())
    got = s.get_state("com.whatsapp", "2.24", "chat_screen")
    assert got is not None
    assert got.name == "chat_screen"
    assert [f.value for f in got.features] == \
        ["com.whatsapp:id/entry", "com.whatsapp:id/send"]
    assert got.fingerprints == ["fp-1"]


def test_state_upsert_overwrites(tmp_path):
    s = _store(tmp_path)
    a = _sample_state()
    s.upsert_state(a)
    a.fingerprints.append("fp-2")
    s.upsert_state(a)
    got = s.get_state("com.whatsapp", "2.24", "chat_screen")
    assert got.fingerprints == ["fp-1", "fp-2"]


def test_find_by_fingerprint(tmp_path):
    s = _store(tmp_path)
    s.upsert_state(_sample_state())
    found = s.find_state_by_fingerprint("com.whatsapp", "2.24", "fp-1")
    assert found is not None and found.name == "chat_screen"
    assert s.find_state_by_fingerprint("com.whatsapp", "2.24", "fp-miss") is None


def test_transition_record_updates_stats(tmp_path):
    s = _store(tmp_path)
    base = StateTransition(
        from_state="main_page", to_state="chat_screen",
        role="chat_item", action="tap",
        app_package="com.whatsapp", app_version="2.24",
    )
    s.record_transition(base, success=True)
    s.record_transition(base, success=True)
    s.record_transition(base, success=False)
    all_t = s.transitions_for("com.whatsapp", "2.24")
    assert len(all_t) == 1
    t = all_t[0]
    assert t.success == 2 and t.failure == 1


def test_transitions_from_filters_to_source_state(tmp_path):
    s = _store(tmp_path)
    s.record_transition(StateTransition(
        from_state="A", to_state="B", role="tap_x", action="tap",
        app_package="com.x", app_version="1"), success=True)
    s.record_transition(StateTransition(
        from_state="B", to_state="C", role="tap_y", action="tap",
        app_package="com.x", app_version="1"), success=True)
    out = s.transitions_from("com.x", "1", "A")
    assert [(t.from_state, t.to_state) for t in out] == [("A", "B")]


def test_cross_version_state_list(tmp_path):
    s = _store(tmp_path)
    s.upsert_state(_sample_state(ver="1.0"))
    s.upsert_state(_sample_state(ver="2.0"))
    names = {st.app_version for st in s.all_versions_states("com.whatsapp")}
    assert names == {"1.0", "2.0"}
