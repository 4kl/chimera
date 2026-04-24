"""Transition helpers: build `via` callables from a sequence of XPath clicks."""
from __future__ import annotations

from typing import Callable, Optional


def compose_clicks(xpaths: list[str], name: Optional[str] = None) -> Callable:
    """Returns a transition function that taps each XPath in order. The
    returned callable accepts arbitrary **kwargs (ignored) so it can be used
    as a transition `via=` interchangeably with context-aware functions."""
    fn_name = name or "click_chain"

    def _fn(driver, **kwargs):
        for xp in xpaths:
            driver.click(xp)

    _fn.__name__ = fn_name
    return _fn
