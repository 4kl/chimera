from chimera.app_graph.declared_state import (DeclaredState, xpath_to_features)
from chimera.app_graph.compose import compose_clicks
from chimera.app_graph.actions import action
from chimera.app_graph.xpath_driver import _role_from_xpath


# ----- xpath_to_features --------------------------------------------------
def test_xpath_translates_resource_id():
    feats = xpath_to_features("//*[@resource-id='com.x:id/send']")
    assert len(feats) == 1
    assert feats[0].kind == "resource_id"
    assert feats[0].value == "com.x:id/send"
    assert feats[0].required is True


def test_xpath_translates_content_desc():
    feats = xpath_to_features("//*[@content-desc='Send']")
    assert [f.kind for f in feats] == ["content_desc"]
    assert feats[0].value == "Send"


def test_xpath_translates_exact_text():
    feats = xpath_to_features("//*[@text='Archived']")
    assert feats[0].kind == "text_contains"  # we translate to text_contains
    assert feats[0].value == "Archived"


def test_xpath_translates_contains_text():
    feats = xpath_to_features(
        "//android.widget.TextView[contains(@text, 'messages')]"
    )
    assert feats[0].kind == "text_contains"
    assert feats[0].value == "messages"


def test_xpath_multi_predicate_falls_back_to_xpath_present():
    xp = "//*[@resource-id='com.x:id/entry' and @text='hi']"
    feats = xpath_to_features(xp)
    assert feats[0].kind == "xpath_present"
    assert feats[0].value == xp


def test_empty_xpath_yields_nothing():
    assert xpath_to_features("") == []


# ----- DeclaredState ------------------------------------------------------
def test_declared_state_features_from_xpaths():
    s = DeclaredState(
        name="chat", xpaths=["//*[@resource-id='com.x:id/send']",
                             "//*[@content-desc='Type a message']"])
    feats = s.features()
    kinds = {f.kind for f in feats}
    assert kinds == {"resource_id", "content_desc"}


def test_declared_state_explicit_features_override_xpaths():
    from chimera.state.models import StateFeature
    s = DeclaredState(
        name="chat",
        xpaths=["//*[@resource-id='com.x:id/send']"],
        features=[StateFeature("resource_id", "com.x:id/manual")],
    )
    feats = s.features()
    assert [f.value for f in feats] == ["com.x:id/manual"]


def test_declared_state_transitions():
    a = DeclaredState(name="A")
    b = DeclaredState(name="B", parent=a)
    def via(driver, **kwargs): pass
    a.to(b, via=via, name="go_b")
    out = a.outbound()
    assert len(out) == 1
    assert out[0].target is b
    assert out[0].name == "go_b"


# ----- compose_clicks -----------------------------------------------------
def test_compose_clicks_calls_each_xpath_in_order():
    clicks: list[str] = []

    class FakeDriver:
        def click(self, xp, **kwargs):
            clicks.append(xp)

    fn = compose_clicks(["//a", "//b", "//c"], name="abc")
    assert fn.__name__ == "abc"
    fn(FakeDriver(), ignored_kwarg="hello")
    assert clicks == ["//a", "//b", "//c"]


# ----- @action decorator captures target state ---------------------------
def test_action_decorator_tags_function_with_state():
    s = DeclaredState(name="chat")

    class FakeApp:
        @action(s)
        def send(self, text): return text

    fn = FakeApp.send
    assert fn._action_state is s
    assert fn._action_end_state is None


def test_action_decorator_with_end_state():
    a = DeclaredState(name="A")
    b = DeclaredState(name="B")

    class FakeApp:
        @action(a, end_state=b)
        def do(self): pass

    assert FakeApp.do._action_state is a
    assert FakeApp.do._action_end_state is b


# ----- role derivation from xpath ----------------------------------------
def test_role_from_xpath_prefers_resource_id_suffix():
    assert _role_from_xpath("//*[@resource-id='com.x:id/send_button']") == \
        "send_button"


def test_role_from_xpath_uses_content_desc_when_no_rid():
    assert _role_from_xpath("//*[@content-desc='Send message']") == \
        "send_message"


def test_role_from_xpath_falls_back_to_hash():
    r = _role_from_xpath("//android.view.ViewGroup[3]/android.widget.Button[1]")
    assert r.startswith("xp_") and len(r) == 11
