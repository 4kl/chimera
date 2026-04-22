from __future__ import annotations

import time
from typing import Optional

from ..core.driver import Driver


def capture(driver: Driver, with_screenshot: bool = True) -> dict:
    """Single snapshot of the current screen: a11y XML + optional screenshot + app ctx."""
    xml = driver.dump_hierarchy()
    png: Optional[bytes] = driver.screenshot() if with_screenshot else None
    try:
        app = driver.current_app()
    except Exception:
        app = {"package": "", "activity": ""}
    return {
        "xml": xml,
        "png": png,
        "ts": time.time(),
        "package": app.get("package", ""),
        "activity": app.get("activity", ""),
    }
