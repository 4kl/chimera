"""Appium-backed driver.

Previously this module wrapped the `uiautomator2` Python client (ATX). It
now wraps Appium's Python client instead. The surface exposed to the rest
of the framework is identical — `xpath(expr)` still returns a locator with
`.wait(timeout) / .click() / .long_click() / .swipe()`, `info` dicts still
have u2-style keys (`resourceName`, `contentDescription`, `text`,
`className`, `bounds`) — so `selector/`, `execution/`, `state/`, and
`app_graph/` were not changed."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Optional

from .errors import ChimeraError

log = logging.getLogger("chimera.driver")

_KEYCODES = {
    "back": 4, "home": 3, "menu": 82, "power": 26,
    "enter": 66, "search": 84, "app_switch": 187,
    "volume_up": 24, "volume_down": 25, "del": 67,
}

_VERSION_NAME_RE = re.compile(r"versionName=([^\s]+)")
_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")


# ---------------------------------------------------------------------------
# u2-compatible shims over Appium WebElement / WebDriver
# ---------------------------------------------------------------------------
class AppiumElementProxy:
    """Wraps an Appium WebElement to mimic uiautomator2's element object.

    The rest of Chimera consumes `.info` as a dict with u2-style keys; this
    proxy synthesizes that from the element's Android attributes."""

    def __init__(self, element):
        self._el = element

    # ---- u2 parity ----
    @property
    def info(self) -> dict:
        def get(attr: str) -> str:
            try:
                v = self._el.get_attribute(attr)
                return v or ""
            except Exception:
                return ""
        return {
            "resourceName": get("resource-id"),
            "contentDescription": get("content-desc"),
            "text": get("text") or (self._el.text or ""),
            "className": get("class"),
            "bounds": get("bounds"),
            "enabled": get("enabled") == "true",
            "clickable": get("clickable") == "true",
            "focused": get("focused") == "true",
        }

    def click(self):
        self._el.click()

    def send_keys(self, text: str, clear: bool = False):
        if clear:
            try:
                self._el.clear()
            except Exception:
                pass
        self._el.send_keys(text)

    def clear(self):
        try:
            self._el.clear()
        except Exception:
            pass

    def long_click(self, duration: float = 1.0):
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ac = ActionChains(self._el.parent)
            ac.click_and_hold(self._el).pause(duration).release().perform()
        except Exception as e:
            raise ChimeraError(f"long_click failed: {e}") from e

    def swipe(self, direction: str):
        try:
            loc = self._el.location
            size = self._el.size
        except Exception:
            return
        cx = loc["x"] + size["width"] // 2
        cy = loc["y"] + size["height"] // 2
        w, h = size["width"], size["height"]
        dx = dy = 0
        d = (direction or "up").lower()
        if d == "up":    dy = -h // 2
        elif d == "down":  dy = h // 2
        elif d == "left":  dx = -w // 2
        elif d == "right": dx = w // 2
        try:
            self._el.parent.swipe(cx, cy, cx + dx, cy + dy, 300)
        except Exception as e:
            log.debug("swipe failed: %s", e)


class XPathLocator:
    """u2-compatible XPath locator. `.wait(timeout)` returns an
    AppiumElementProxy or None; the other methods act on the first match."""

    def __init__(self, driver: "Driver", expr: str):
        self._driver = driver
        self._expr = expr

    def wait(self, timeout: float = 3.0) -> Optional[AppiumElementProxy]:
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            el = WebDriverWait(self._driver.raw, timeout).until(
                EC.presence_of_element_located((By.XPATH, self._expr))
            )
            return AppiumElementProxy(el)
        except TimeoutException:
            return None
        except Exception as e:
            log.debug("xpath wait error on %r: %s", self._expr, e)
            return None

    @property
    def exists(self) -> bool:
        return self.wait(timeout=0.2) is not None

    def click(self):
        proxy = self.wait(self._driver.default_wait)
        if proxy is None:
            raise ChimeraError(f"xpath not found: {self._expr}")
        proxy.click()

    def long_click(self, duration: float = 1.0):
        proxy = self.wait(self._driver.default_wait)
        if proxy is None:
            raise ChimeraError(f"xpath not found: {self._expr}")
        proxy.long_click(duration=duration)

    def swipe(self, direction: str):
        proxy = self.wait(self._driver.default_wait)
        if proxy is None:
            raise ChimeraError(f"xpath not found: {self._expr}")
        proxy.swipe(direction)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
class Driver:
    def __init__(self, impl, serial: Optional[str] = None,
                 default_wait: float = 3.0):
        self._d = impl
        self._serial = serial
        self.default_wait = default_wait

    @property
    def raw(self):
        """Underlying Appium WebDriver; escape hatch for advanced calls."""
        return self._d

    @classmethod
    def connect(cls,
                serial: Optional[str] = None,
                appium_url: Optional[str] = None,
                package: Optional[str] = None,
                **extra_caps) -> "Driver":
        """Connect to an Appium server and start a UiAutomator2 session.

        Requires:
          - `pip install Appium-Python-Client`
          - An Appium server reachable at APPIUM_URL (default
            http://127.0.0.1:4723). Start with e.g.
            `appium --allow-insecure adb_shell`
        """
        try:
            from appium import webdriver
            from appium.options.android import UiAutomator2Options
        except ImportError as e:
            raise ChimeraError(
                "Appium-Python-Client not installed. "
                "`pip install Appium-Python-Client`."
            ) from e

        url = appium_url or os.environ.get("APPIUM_URL",
                                           "http://127.0.0.1:4723")
        serial = serial or os.environ.get("ANDROID_SERIAL") or None

        opts = UiAutomator2Options()
        opts.platform_name = "Android"
        opts.automation_name = "UiAutomator2"
        opts.no_reset = True
        opts.new_command_timeout = 600
        # Fast element finders — they still return WebElements we can .click()
        try:
            opts.set_capability("appium:disableIdLocatorAutocompletion", True)
        except Exception:
            pass
        if serial:
            opts.udid = serial
        if package:
            opts.app_package = package

        for k, v in extra_caps.items():
            try:
                setattr(opts, k, v)
            except Exception:
                opts.set_capability(k, v)

        log.info("connecting to Appium at %s (serial=%s)", url, serial or "auto")
        try:
            d = webdriver.Remote(url, options=opts)
        except Exception as e:
            raise ChimeraError(
                f"failed to connect to Appium at {url}. "
                f"Is the server running?  ({e})"
            ) from e
        return cls(d, serial=serial)

    # ---- perception ---------------------------------------------------------
    def dump_hierarchy(self) -> str:
        return self._d.page_source

    def screenshot(self) -> bytes:
        return self._d.get_screenshot_as_png()

    def current_app(self) -> dict:
        try:
            pkg = self._d.current_package
        except Exception:
            pkg = ""
        try:
            act = self._d.current_activity
        except Exception:
            act = ""
        return {"package": pkg or "", "activity": act or ""}

    # ---- app info / shell ---------------------------------------------------
    def app_info(self, package: str) -> dict:
        """Returns {'versionName', 'versionCode', 'packageName'} via dumpsys.
        Uses host-side adb when available, else Appium's mobile:shell."""
        out = self.shell(
            f"dumpsys package {package} | grep -E 'versionName|versionCode'")
        vn = _VERSION_NAME_RE.search(out or "")
        vc = _VERSION_CODE_RE.search(out or "")
        return {
            "versionName": vn.group(1) if vn else "",
            "versionCode": vc.group(1) if vc else "",
            "packageName": package,
        }

    def shell(self, cmd: str, timeout: float = 10.0) -> str:
        """Run an adb shell command. Prefers host-side `adb` (no special
        Appium config needed) and falls back to Appium's `mobile: shell`."""
        adb = shutil.which("adb")
        if adb:
            argv = [adb]
            if self._serial:
                argv += ["-s", self._serial]
            argv += ["shell", "sh", "-c", cmd]
            try:
                res = subprocess.run(argv, capture_output=True, text=True,
                                     timeout=timeout)
                return (res.stdout or "") + (res.stderr or "")
            except Exception as e:
                log.debug("host adb shell failed (%s); trying Appium", e)

        try:
            parts = cmd.strip().split(maxsplit=1)
            script = {"command": parts[0],
                      "args": parts[1].split() if len(parts) > 1 else [],
                      "includeStderr": True,
                      "timeout": int(timeout * 1000)}
            res = self._d.execute_script("mobile: shell", script)
            if isinstance(res, dict):
                return (str(res.get("stdout", "")) +
                        str(res.get("stderr", "")))
            return str(res or "")
        except Exception as e:
            log.debug("mobile:shell failed: %s", e)
            return ""

    # ---- actions -----------------------------------------------------------
    def xpath(self, expr: str) -> XPathLocator:
        return XPathLocator(self, expr)

    def click_xy(self, x: int, y: int):
        try:
            self._d.execute_script("mobile: clickGesture",
                                   {"x": int(x), "y": int(y)})
            return
        except Exception:
            pass
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ac = ActionChains(self._d)
            ac.w3c_actions.pointer_action.move_to_location(int(x), int(y)) \
                .click().perform()
        except Exception as e:
            raise ChimeraError(f"click_xy failed: {e}") from e

    def send_keys(self, text: str, clear: bool = True):
        """Send keys to the currently-focused element (EditText). Attempts
        to clear it first when `clear` is True."""
        try:
            active = self._d.switch_to.active_element
        except Exception:
            active = None

        if active is not None:
            if clear:
                try:
                    active.clear()
                except Exception:
                    pass
            try:
                active.send_keys(text)
                return
            except Exception as e:
                log.debug("active_element.send_keys failed: %s", e)

        # Last-ditch: type via Android input command
        try:
            self._d.execute_script("mobile: type", {"text": text})
        except Exception as e:
            raise ChimeraError(f"send_keys failed: {e}") from e

    def clear_text(self):
        try:
            self._d.switch_to.active_element.clear()
        except Exception:
            pass

    def press(self, key):
        if isinstance(key, int):
            self._d.press_keycode(int(key))
            return
        k = str(key).strip().lower()
        if k == "back":
            self._d.back()
            return
        code = _KEYCODES.get(k)
        if code is not None:
            self._d.press_keycode(code)
            return
        if k.isdigit():
            self._d.press_keycode(int(k))
            return
        raise ChimeraError(f"unknown key: {key!r}")

    def launch(self, package: str):
        try:
            self._d.activate_app(package)
            return
        except Exception:
            pass
        try:
            self._d.execute_script("mobile: activateApp", {"appId": package})
        except Exception as e:
            raise ChimeraError(f"launch {package!r} failed: {e}") from e

    def stop(self, package: str):
        try:
            self._d.terminate_app(package)
        except Exception:
            pass

    def wait_idle(self, timeout: float = 2.0):
        # Appium has no universal idle signal; callers (executor) poll
        # screen fingerprints. Tiny sleep here just to yield.
        time.sleep(min(timeout, 0.3))

    # ---- introspection -----------------------------------------------------
    def device_info(self) -> dict[str, Any]:
        try:
            info = self._d.execute_script("mobile: deviceInfo")
            if isinstance(info, dict):
                return info
        except Exception:
            pass
        try:
            caps = dict(self._d.capabilities or {})
            return {
                "brand": caps.get("deviceManufacturer", ""),
                "model": caps.get("deviceModel", ""),
                "version": caps.get("platformVersion", ""),
                "udid": caps.get("deviceUDID", ""),
            }
        except Exception:
            return {}

    def quit(self):
        try:
            self._d.quit()
        except Exception:
            pass
