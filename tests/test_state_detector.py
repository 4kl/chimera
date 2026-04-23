from chimera.core.models import Frame, UINode
from chimera.state.detector import (
    MATCH_THRESHOLD, StateDetector, _auto_features, _match_score,
    _sanitize_name,
)
from chimera.state.models import StateFeature, UIState


def _node(rid="", text="", desc="", cls="View", idx=0):
    return UINode(
        index=idx, cls=cls, text=text, content_desc=desc,
        resource_id=rid, package="com.whatsapp", bounds=(0, 0, 10, 10),
        clickable=bool(rid or desc or text), enabled=True, focused=False,
        xpath_abs=f"/{cls}[1]",
    )


def _frame(flat):
    return Frame(root=None, flat=flat, fp="fp-1",
                 package="com.whatsapp", activity="", png=None)


def test_score_full_match():
    flat = [_node(rid="com.whatsapp:id/entry"),
            _node(rid="com.whatsapp:id/send", idx=1)]
    state = UIState(
        name="chat_screen", app_package="com.whatsapp",
        features=[
            StateFeature("resource_id", "com.whatsapp:id/entry"),
            StateFeature("resource_id", "com.whatsapp:id/send"),
        ],
    )
    assert _match_score(_frame(flat), state) == 1.0


def test_score_partial_match_below_threshold():
    flat = [_node(rid="com.whatsapp:id/entry")]
    state = UIState(
        name="chat_screen", app_package="com.whatsapp",
        features=[
            StateFeature("resource_id", "com.whatsapp:id/entry"),
            StateFeature("resource_id", "com.whatsapp:id/send"),
            StateFeature("resource_id", "com.whatsapp:id/attach"),
        ],
    )
    s = _match_score(_frame(flat), state)
    assert 0.3 <= s <= 0.4
    assert s < MATCH_THRESHOLD


def test_required_feature_zeros_score_when_missing():
    flat = [_node(rid="com.whatsapp:id/entry")]
    state = UIState(
        name="chat_screen", app_package="com.whatsapp",
        features=[
            StateFeature("resource_id", "com.whatsapp:id/entry"),
            StateFeature("resource_id", "com.whatsapp:id/send", required=True),
        ],
    )
    assert _match_score(_frame(flat), state) == 0.0


def test_score_with_text_contains():
    flat = [_node(text="Type a message"), _node(rid="x:id/entry", idx=1)]
    state = UIState(
        name="chat_screen", app_package="com.whatsapp",
        features=[
            StateFeature("text_contains", "message"),
            StateFeature("resource_id", "x:id/entry"),
        ],
    )
    assert _match_score(_frame(flat), state) == 1.0


def test_score_with_class_min():
    flat = [_node(cls="android.widget.RecyclerView"),
            _node(cls="android.widget.TextView", idx=1),
            _node(cls="android.widget.TextView", idx=2)]
    state = UIState(
        name="main_page", app_package="com.whatsapp",
        features=[StateFeature("class_min", "android.widget.TextView:2")],
    )
    assert _match_score(_frame(flat), state) == 1.0


def test_auto_features_prefers_app_scoped_rids():
    flat = [_node(rid="android:id/content"),
            _node(rid="com.whatsapp:id/main_pane", idx=1),
            _node(rid="com.whatsapp:id/toolbar", idx=2),
            _node(rid="com.whatsapp:id/main_pane", idx=3)]  # duplicate
    feats = _auto_features(_frame(flat))
    # android:id/* excluded; app-scoped rids kept.
    vals = {f.value for f in feats}
    assert "android:id/content" not in vals
    assert "com.whatsapp:id/toolbar" in vals


def test_sanitize_name():
    assert _sanitize_name("Chat Screen") == "chat_screen"
    assert _sanitize_name("Main-Page!!") == "main_page"
    assert _sanitize_name("") == "unknown"
    assert _sanitize_name("  Settings_Page  ") == "settings_page"
