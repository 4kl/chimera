"""Thin wrapper over uiautomator2 so the rest of the code never touches the
driver library directly. Keeps the door open for swapping in Appium later."""
from __future__ import annotations

from typing import Any, Optional

from .errors import ChimeraError


class Driver:
    def __init__(self, impl):
        self._d = impl

    @classmethod
    def connect(cls, serial: Optional[str] = None) -> "Driver":
        try:
            import uiautomator2 as u2
        except ImportError as e:
            raise ChimeraError(
                "uiautomator2 not installed. `pip install uiautomator2`.") from e
        d = u2.connect(serial) if serial else u2.connect()
        return cls(d)

    # ---- perception ----
    def dump_hierarchy(self) -> str:
        return self._d.dump_hierarchy(compressed=False)

    def screenshot(self) -> bytes:
        img = self._d.screenshot(format="pillow")
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def current_app(self) -> dict:
        return self._d.app_current()

    def app_info(self, package: str) -> dict:
        """Returns {'versionName': ..., 'versionCode': ..., 'packageName': ...}."""
        return self._d.app_info(package)

    def shell(self, cmd: str, timeout: float = 10.0) -> str:
        out = self._d.shell(cmd, timeout=timeout)
        # uiautomator2 returns a ShellResponse(output=..., exit_code=...)
        return getattr(out, "output", str(out))

    # ---- actions ----
    def xpath(self, expr: str):
        return self._d.xpath(expr)

    def click_xy(self, x: int, y: int):
        self._d.click(x, y)

    def send_keys(self, text: str, clear: bool = True):
        if clear:
            self._d.clear_text()
        self._d.send_keys(text)

    def press(self, key: str):
        self._d.press(key)

    def launch(self, package: str):
        self._d.app_start(package, stop=False)

    def stop(self, package: str):
        self._d.app_stop(package)

    def wait_idle(self, timeout: float = 2.0):
        try:
            self._d.wait_activity(".*", timeout=timeout)
        except Exception:
            pass

    # ---- introspection ----
    def device_info(self) -> dict[str, Any]:
        return self._d.device_info
