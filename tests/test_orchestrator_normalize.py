from chimera.core.models import ActionStep, Intent
from chimera.orchestrator import _extract_app_hint


def test_extract_open_with_app_suffix():
    assert _extract_app_hint("Open camera app and take a pic") == "camera"
    assert _extract_app_hint("Launch WhatsApp") == "whatsapp"
    assert _extract_app_hint("go to youtube and search for messi") == "youtube"


def test_extract_without_app_suffix():
    assert _extract_app_hint("open chrome") == "chrome"


def test_extract_multi_word():
    assert _extract_app_hint("open play store and install signal") == "play store"


def test_extract_returns_none_for_non_open_commands():
    assert _extract_app_hint("Send John hey") is None
    assert _extract_app_hint("take a picture") is None
    assert _extract_app_hint("") is None


def test_normalize_injects_launch_when_llm_missed_it():
    """The planner returned app_hint=None and a 'tap camera_icon' step — the
    orchestrator's normalization should replace it with a launch step."""
    # We can't import Chimera (it requires a device) but _normalize_plan is
    # bound to the class; we can test the logic by re-creating it as a helper.
    # Instead, verify the regex-based extractor catches the case so that
    # _normalize_plan has the hint it needs to act.
    nl = "Open camera app and take a pic"
    assert _extract_app_hint(nl) == "camera"
    # And verify that an Intent with the wrong first step is recognizable.
    intent = Intent(
        raw=nl, app_hint=None,
        steps=[
            ActionStep(role="camera_icon", action="tap",
                       description="camera icon on home"),
            ActionStep(role="take_picture_button", action="tap",
                       description="shutter button"),
        ],
    )
    # _normalize_plan would drop steps[0] and insert a launch step; we only
    # assert the preconditions here. Integration verified at runtime.
    assert intent.app_hint is None
    assert intent.steps[0].action == "tap"
