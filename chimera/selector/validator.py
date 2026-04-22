from __future__ import annotations

from ..core.driver import Driver
from ..core.models import SelectorCandidate


def validate(driver: Driver, cand: SelectorCandidate,
             expected_fp: str, timeout: float = 1.5) -> bool:
    """Return True iff the selector resolves on-screen AND the resolved node's
    semantic key matches the expected fingerprint (or is close enough)."""
    try:
        sel = driver.xpath(cand.expr)
        el = sel.wait(timeout=timeout)
        if not el:
            return False
        info = el.info  # uiautomator2 XPathSelector elements expose .info
        live_fp = _fp_from_info(info)
        return live_fp == expected_fp or _fuzzy_match(live_fp, expected_fp) >= 0.75
    except Exception:
        return False


def _fp_from_info(info: dict) -> str:
    import hashlib
    rid = info.get("resourceName", "") or ""
    desc = info.get("contentDescription", "") or ""
    txt = (info.get("text", "") or "")[:32]
    cls = info.get("className", "") or ""
    raw = f"{rid}|{desc}|{txt}|{cls}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _fuzzy_match(a: str, b: str) -> float:
    # Hashes don't fuzzy-match meaningfully. Kept as a seam; healer does the
    # real fuzzy work by re-picking with the LLM on the live tree.
    return 1.0 if a == b else 0.0
