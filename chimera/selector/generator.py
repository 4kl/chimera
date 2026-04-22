from __future__ import annotations

import re
from typing import Optional

from ..core.models import SelectorCandidate, UINode
from .ranker import rank


def _esc(s: str) -> str:
    # single quotes are the delimiter; double them is not valid XPath, so swap.
    if "'" in s and '"' not in s:
        return f'"{s}"'
    return f"'{s}'"


def _xpath_value(attr: str, value: str) -> str:
    # produce the correct xpath literal (handling quotes)
    if "'" in value and '"' in value:
        # concat trick
        parts = value.split("'")
        joined = ", \"'\", ".join(f"'{p}'" for p in parts)
        return f"[@{attr}=concat({joined})]"
    return f"[@{attr}={_esc(value)}]"


def _unique(flat: list[UINode], **attrs) -> bool:
    def match(n: UINode) -> bool:
        for k, v in attrs.items():
            if getattr(n, k, "") != v:
                return False
        return True
    return sum(1 for n in flat if match(n)) == 1


class SelectorGenerator:
    STABILITY = {
        "resource_id": 0.95,
        "resource_id_text": 0.90,
        "content_desc": 0.85,
        "content_desc_contains": 0.70,
        "text_exact": 0.70,
        "text_contains": 0.60,
        "rel_xpath": 0.55,
        "class_index_local": 0.40,
        "abs_xpath": 0.25,
    }

    def generate(self, node: UINode, flat: list[UINode]) -> list[SelectorCandidate]:
        cands: list[SelectorCandidate] = []

        if node.resource_id:
            if _unique(flat, resource_id=node.resource_id):
                cands.append(SelectorCandidate(
                    f"//*{_xpath_value('resource-id', node.resource_id)}",
                    "resource_id", self.STABILITY["resource_id"]))
            elif node.text:
                cands.append(SelectorCandidate(
                    f"//*{_xpath_value('resource-id', node.resource_id)}"
                    f"{_xpath_value('text', node.text)}",
                    "resource_id_text", self.STABILITY["resource_id_text"]))
            else:
                # resource_id + sibling index
                cands.append(SelectorCandidate(
                    f"(//*{_xpath_value('resource-id', node.resource_id)})"
                    f"[{_sibling_index(flat, node, by='resource_id')}]",
                    "resource_id", self.STABILITY["resource_id"] - 0.1))

        if node.content_desc:
            cands.append(SelectorCandidate(
                f"//*{_xpath_value('content-desc', node.content_desc)}",
                "content_desc", self.STABILITY["content_desc"]))
            prefix = node.content_desc[:20]
            if prefix and prefix != node.content_desc:
                cands.append(SelectorCandidate(
                    f"//*[contains(@content-desc,{_esc(prefix)})]",
                    "content_desc_contains",
                    self.STABILITY["content_desc_contains"]))

        if node.text:
            cands.append(SelectorCandidate(
                f"//{node.cls}{_xpath_value('text', node.text)}",
                "text_exact", self.STABILITY["text_exact"]))
            prefix = node.text[:16]
            if prefix and prefix != node.text:
                cands.append(SelectorCandidate(
                    f"//*[contains(@text,{_esc(prefix)})]",
                    "text_contains", self.STABILITY["text_contains"]))

        anchor = _nearest_anchor(node)
        if anchor is not None and anchor is not node:
            rel = _relative_path(anchor, node)
            if rel:
                cands.append(SelectorCandidate(
                    f"//*{_xpath_value('resource-id', anchor.resource_id)}{rel}",
                    "rel_xpath", self.STABILITY["rel_xpath"]))

        cands.append(SelectorCandidate(
            _class_index_local(node), "class_index_local",
            self.STABILITY["class_index_local"]))

        cands.append(SelectorCandidate(
            node.xpath_abs or f"//{node.cls}", "abs_xpath",
            self.STABILITY["abs_xpath"]))

        # dedupe by expr, keep highest score
        seen: dict[str, SelectorCandidate] = {}
        for c in cands:
            if c.expr in seen:
                if c.score > seen[c.expr].score:
                    seen[c.expr] = c
            else:
                seen[c.expr] = c

        return rank(list(seen.values()), node, flat)


def _sibling_index(flat: list[UINode], node: UINode, by: str) -> int:
    matches = [n for n in flat if getattr(n, by, "") == getattr(node, by, "")]
    for i, n in enumerate(matches, start=1):
        if n is node:
            return i
    return 1


def _nearest_anchor(node: UINode) -> Optional[UINode]:
    cur = node.parent
    while cur is not None:
        if cur.resource_id:
            return cur
        cur = cur.parent
    return None


def _relative_path(anchor: UINode, target: UINode) -> str:
    # walk from anchor down to target, emitting /cls[idx] per hop
    chain: list[UINode] = []
    cur = target
    while cur is not None and cur is not anchor:
        chain.append(cur)
        cur = cur.parent
    if cur is not anchor:
        return ""
    chain.reverse()
    parts = []
    for n in chain:
        if n.parent is None:
            continue
        idx = 1 + sum(1 for s in n.parent.children
                      if s is not n and s.cls == n.cls
                      and n.parent.children.index(s) < n.parent.children.index(n))
        parts.append(f"/{n.cls}[{idx}]")
    return "".join(parts)


def _class_index_local(node: UINode) -> str:
    """Sibling-aware class path going up at most 2 levels — cheap and often
    stable across text edits."""
    chain = []
    cur = node
    for _ in range(3):
        if cur is None:
            break
        if cur.parent is None:
            chain.append(f"/{cur.cls}")
            break
        siblings = [s for s in cur.parent.children if s.cls == cur.cls]
        idx = siblings.index(cur) + 1 if cur in siblings else 1
        chain.append(f"/{cur.cls}[{idx}]")
        cur = cur.parent
    return "/" + "".join(reversed(chain))
