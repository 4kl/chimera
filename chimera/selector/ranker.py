from __future__ import annotations

import re

from ..core.models import SelectorCandidate, UINode


_DYNAMIC_PATTERNS = [
    re.compile(r"\d{1,2}:\d{2}"),           # times like 09:42
    re.compile(r"\b\d+\s*(new|unread|msg)", re.I),
    re.compile(r"^\d+$"),                    # pure numbers
    re.compile(r"\b(yesterday|today|ago|min|hour|day|sec)\b", re.I),
]


def _looks_dynamic(s: str) -> bool:
    if not s:
        return False
    return any(p.search(s) for p in _DYNAMIC_PATTERNS)


def rank(cands: list[SelectorCandidate], node: UINode,
         flat: list[UINode]) -> list[SelectorCandidate]:
    for c in cands:
        s = c.score
        # Dynamic-text penalty for text-based selectors
        if c.strategy.startswith("text") and _looks_dynamic(node.text):
            s -= 0.25
        # Pure-digit text penalty
        if c.strategy == "text_exact" and node.text and node.text.strip().isdigit():
            s -= 0.2
        # Reward if the selector truly uniquely identifies on this screen
        # (approximation: for attr-based selectors, check attribute uniqueness)
        if "@resource-id=" in c.expr and _unique_by_rid(flat, node.resource_id):
            s += 0.03
        c.score = max(0.0, min(1.0, s))
    cands.sort(key=lambda c: -c.score)
    return cands


def _unique_by_rid(flat: list[UINode], rid: str) -> bool:
    return rid != "" and sum(1 for n in flat if n.resource_id == rid) == 1
