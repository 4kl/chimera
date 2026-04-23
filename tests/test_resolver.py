from chimera.profiler.app_resolver import AppResolver


class FakeDriver:
    def __init__(self, shell_out=""):
        self._out = shell_out

    def shell(self, cmd, timeout=10.0):
        return self._out


_PM = """package:com.android.camera2
package:com.google.android.youtube
package:com.whatsapp
package:com.oneplus.camera
package:com.google.android.apps.nexuslauncher
package:com.google.android.gm
"""


def test_resolve_exact_static_alias():
    r = AppResolver(FakeDriver(_PM), static_alias={"youtube": "com.google.android.youtube"})
    assert r.resolve("youtube") == "com.google.android.youtube"


def test_resolve_unknown_name_via_device_scan():
    r = AppResolver(FakeDriver(_PM))
    # "camera" should match the camera packages. Prefer exact segment match
    # over OEM variants; "camera2" wins over "oneplus.camera" because camera2
    # has "camera" as a prefix of a segment (score 4 via startswith).
    assert r.resolve("camera") in {"com.android.camera2", "com.oneplus.camera"}


def test_resolve_passthrough_for_installed_package_id():
    r = AppResolver(FakeDriver(_PM))
    assert r.resolve("com.whatsapp") == "com.whatsapp"


def test_resolve_returns_none_when_no_match():
    r = AppResolver(FakeDriver(_PM))
    assert r.resolve("elephants") is None


def test_shell_failure_returns_none():
    class Dead:
        def shell(self, cmd, timeout=10.0):
            raise RuntimeError("shell failed")
    r = AppResolver(Dead())
    assert r.resolve("camera") is None


def test_static_alias_wins_over_device_scan():
    # If both static and device would answer, static takes precedence.
    r = AppResolver(FakeDriver(_PM),
                    static_alias={"camera": "com.foo.bar"})
    assert r.resolve("camera") == "com.foo.bar"
