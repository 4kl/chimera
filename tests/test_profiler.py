from chimera.profiler.app_profiler import AppProfiler, normalize_app_name


class FakeDriver:
    """Minimal driver stub. We simulate the three paths AppProfiler uses:
    app_info(), current_app(), and shell()."""
    def __init__(self, info=None, shell_out="", current=""):
        self._info = info
        self._shell_out = shell_out
        self._current = current

    def app_info(self, package):
        if self._info is None:
            raise RuntimeError("app_info not available")
        return self._info

    def current_app(self):
        return {"package": self._current}

    def shell(self, cmd, timeout=10.0):
        return self._shell_out


def test_profile_via_app_info():
    d = FakeDriver(info={"versionName": "2.24.10", "versionCode": "241000"})
    p = AppProfiler(d).of("com.whatsapp")
    assert p.package == "com.whatsapp"
    assert p.version == "2.24.10"
    assert p.version_code == "241000"


def test_profile_fallback_to_dumpsys():
    shell_out = "    versionName=9.1.2\n    versionCode=91200"
    d = FakeDriver(info=None, shell_out=shell_out)
    p = AppProfiler(d).of("com.example")
    assert p.version == "9.1.2"
    assert p.version_code == "91200"


def test_profile_missing_data_returns_blanks():
    d = FakeDriver(info=None, shell_out="")
    p = AppProfiler(d).of("com.example")
    assert p.version == ""
    assert p.key == "com.example@unknown"


def test_profile_caches_results():
    calls = {"n": 0}

    class Counting(FakeDriver):
        def app_info(self, package):
            calls["n"] += 1
            return {"versionName": "1.0", "versionCode": "1"}

    ap = AppProfiler(Counting())
    ap.of("com.foo")
    ap.of("com.foo")
    ap.of("com.foo")
    assert calls["n"] == 1


def test_normalize_app_name():
    assert normalize_app_name("com.whatsapp") == "whatsapp"
    assert normalize_app_name("org.telegram.messenger") == "telegram"
    # Google apps are ambiguous; logging-only so we accept the second segment.
    assert normalize_app_name("com.google.android.gm") == "google"
    assert normalize_app_name("") == ""
    assert normalize_app_name("singleword") == "singleword"
