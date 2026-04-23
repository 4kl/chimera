from chimera.state.models import StateFeature, StateTransition, UIState


def test_feature_roundtrip_dict():
    f = StateFeature(kind="resource_id", value="com.x:id/send",
                     weight=1.5, required=True)
    d = f.to_dict()
    assert StateFeature.from_dict(d) == f


def test_state_merge_observation_grows_fingerprints_and_roles():
    s = UIState(name="chat_screen", app_package="com.whatsapp")
    assert s.confidence == 0.5
    s.merge_observation("fp-1", roles=["send_button", "message_input"])
    s.merge_observation("fp-1", roles=["send_button"])     # dedup
    s.merge_observation("fp-2", roles=["emoji_button"])
    assert s.fingerprints == ["fp-1", "fp-2"]
    assert set(s.allowed_roles) == {"send_button", "message_input", "emoji_button"}
    assert 0.5 < s.confidence <= 1.0


def test_transition_confidence_is_laplace_smoothed():
    t = StateTransition(
        from_state="A", to_state="B", role="tap_x", action="tap",
        app_package="com.app",
    )
    assert t.confidence == 0.5    # zero samples
    t.success = 3
    t.failure = 1
    assert abs(t.confidence - (3 + 1) / (4 + 2)) < 1e-9
    t.success = 100
    t.failure = 0
    assert t.confidence > 0.98
