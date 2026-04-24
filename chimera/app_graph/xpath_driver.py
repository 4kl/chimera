"""XPath-first hybrid driver.

Exposes a PUMA-like surface (`click`, `send_keys`, `is_present`,
`get_elements`, `long_click_element`, `back`, …). Each call tries the
supplied XPath directly against the device. If the XPath resolves and the
action succeeds → zero LLM, fully deterministic. If it doesn't (app updated,
element renamed), the driver falls back to Chimera's selector memory + LLM
discovery to locate an equivalent element, caches the repaired XPath under
the current (app, version, state, role), and replays the action.

This is the mechanism that makes a PUMA-style XPath collection automatically
self-heal across app updates."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..core.errors import ChimeraError, ExecutionError
from ..core.models import ActionStep, Frame, RunCtx, SelectorCandidate

log = logging.getLogger("chimera.xpath_driver")


class XPathNotFound(ChimeraError):
    pass


class XPathDriver:
    """Thin facade that a PUMA-style AppGraph talks to. The underlying work
    is delegated to the Chimera Executor / Driver / selector engine so every
    fallback path still participates in the learning + healing pipeline."""

    def __init__(self, chimera, ctx_provider: Callable[[], RunCtx]):
        # `chimera` is the orchestrator instance; exposes .d, .exec, .mem
        # `ctx_provider` returns the live RunCtx (with current app+version)
        self._chimera = chimera
        self._ctx = ctx_provider
        self._default_timeout = 3.0

    # -------------- primitive queries --------------
    def is_present(self, xpath: str, timeout: float = 1.5) -> bool:
        try:
            el = self._chimera.d.xpath(xpath)
            return bool(el.wait(timeout=timeout))
        except Exception:
            return False

    def get_element(self, xpath: str, timeout: Optional[float] = None):
        t = self._default_timeout if timeout is None else timeout
        el = self._chimera.d.xpath(xpath)
        node = el.wait(timeout=t)
        if not node:
            raise XPathNotFound(xpath)
        return node

    # -------------- actions --------------
    def click(self, xpath: str, *, role: Optional[str] = None,
              timeout: Optional[float] = None,
              state_name: Optional[str] = None):
        """Click the element matching `xpath`. If the XPath doesn't resolve
        within `timeout`, fall back to LLM discovery using `role` (or the
        XPath itself as a description) and cache the repaired selector."""
        t = self._default_timeout if timeout is None else timeout
        try:
            el = self._chimera.d.xpath(xpath)
            node = el.wait(timeout=t)
            if node:
                el.click()
                return
        except Exception as e:
            log.debug("xpath click raw error on %s: %s", xpath, e)
        self._heal_and_retry(
            xpath=xpath, role=role, action="tap", value=None,
            state_name=state_name)

    def send_keys(self, xpath: str, text: str, *,
                  role: Optional[str] = None,
                  timeout: Optional[float] = None,
                  state_name: Optional[str] = None):
        t = self._default_timeout if timeout is None else timeout
        try:
            el = self._chimera.d.xpath(xpath)
            node = el.wait(timeout=t)
            if node:
                el.click()
                self._chimera.d.send_keys(text, clear=True)
                return
        except Exception as e:
            log.debug("xpath send_keys raw error on %s: %s", xpath, e)
        self._heal_and_retry(
            xpath=xpath, role=role, action="type", value=text,
            state_name=state_name)

    def back(self):
        self._chimera.d.press("back")
        time.sleep(0.4)

    def long_click(self, xpath: str, *, role: Optional[str] = None,
                   duration: float = 1.0, timeout: float = 3.0):
        try:
            el = self._chimera.d.xpath(xpath)
            node = el.wait(timeout=timeout)
            if node:
                # uiautomator2 supports long_click via info or xpath
                self._chimera.d.xpath(xpath).long_click()
                return
        except Exception:
            pass
        # No clean healing pathway for long_click yet — raise so the caller
        # sees a loud failure rather than a silent wrong-element tap.
        raise XPathNotFound(f"long_click target missing: {xpath}")

    # -------------- healing --------------
    def _heal_and_retry(self, *, xpath: str, role: Optional[str],
                        action: str, value: Optional[str],
                        state_name: Optional[str]):
        """The XPath didn't resolve. Ask Chimera's executor to find an
        equivalent element by role/description; it will cache the result in
        selector memory so future calls are XPath-fast again (via the top
        candidate in the resulting SelectorBundle, which is the new XPath)."""
        ctx = self._ctx()
        role = role or _role_from_xpath(xpath)
        description = (f"element previously matched by xpath {xpath!r}"
                       f"{f' in state {state_name}' if state_name else ''}")
        log.info("xpath-heal: %s action=%s role=%s", xpath, action, role)
        step = ActionStep(role=role, action=action, value=value,
                          description=description,
                          target_state=state_name)
        try:
            self._chimera.exec.run_step(step, ctx)
        except ExecutionError as e:
            raise XPathNotFound(
                f"heal failed for xpath {xpath!r}: {e}") from e


# ---- naming ----
def _role_from_xpath(xpath: str) -> str:
    """Derive a stable role name from a raw XPath so the selector cache has
    something to key off of. Heuristic: prefer the last segment of a
    resource-id; else a short hash."""
    import hashlib
    import re
    m = re.search(r"@resource-id\s*=\s*['\"][^'\"]*[:/]id/([a-zA-Z0-9_]+)",
                  xpath)
    if m:
        return m.group(1)
    m = re.search(r"@content-desc\s*=\s*['\"]([^'\"]+)['\"]", xpath)
    if m:
        return _slug(m.group(1))
    m = re.search(r"@text\s*=\s*['\"]([^'\"]+)['\"]", xpath)
    if m:
        return _slug(m.group(1))
    return "xp_" + hashlib.sha1(xpath.encode()).hexdigest()[:8]


def _slug(s: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.strip().lower())
    return s.strip("_") or "elem"
