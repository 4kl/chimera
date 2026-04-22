from __future__ import annotations

from ..core.driver import Driver
from ..core.models import ActionStep, SelectorCandidate


def perform(driver: Driver, cand: SelectorCandidate, step: ActionStep) -> bool:
    """Apply `step` using `cand` as the target selector.
    Returns True on success, False otherwise."""
    try:
        if step.action == "launch":
            pkg = step.value or ""
            if pkg:
                driver.launch(pkg)
                return True
            return False

        if step.action == "back":
            driver.press("back")
            return True

        el = driver.xpath(cand.expr)
        if step.action == "tap":
            el.click()
            return True
        if step.action == "type":
            el.click()
            driver.send_keys(step.value or "", clear=True)
            return True
        if step.action == "swipe":
            direction = (step.value or "up").lower()
            el.swipe(direction)
            return True
        if step.action == "wait":
            node = el.wait(timeout=5)
            return node is not None
        return False
    except Exception:
        return False
