from __future__ import annotations

from typing import Optional

import lxml.etree as ET

from ..core.models import UINode


def _bounds(s: Optional[str]) -> tuple[int, int, int, int]:
    if not s:
        return (0, 0, 0, 0)
    # format: "[x1,y1][x2,y2]"
    try:
        a, b = s.split("][")
        x1, y1 = a.lstrip("[").split(",")
        x2, y2 = b.rstrip("]").split(",")
        return (int(x1), int(y1), int(x2), int(y2))
    except Exception:
        return (0, 0, 0, 0)


def _bool(s: Optional[str]) -> bool:
    return s == "true"


def parse(xml_str: str) -> tuple[UINode, list[UINode]]:
    """Parse a uiautomator hierarchy dump into a UINode tree + flat index.

    XPath policy: each node gets an absolute xpath built from class + its
    index among same-class siblings, matching what uiautomator exposes.
    """
    root_el = ET.fromstring(xml_str.encode())
    # <hierarchy> wraps one or more <node>
    top_nodes = list(root_el) if root_el.tag == "hierarchy" else [root_el]

    flat: list[UINode] = []

    def walk(el, parent: Optional[UINode], parent_path: str) -> UINode:
        cls = el.get("class", el.tag)
        # count siblings with the same class already attached to parent
        if parent is not None:
            idx_in_cls = sum(1 for s in parent.children if s.cls == cls)
        else:
            idx_in_cls = 0
        xp = f"{parent_path}/{cls}[{idx_in_cls + 1}]"

        node = UINode(
            index=len(flat),
            cls=cls,
            text=el.get("text", "") or "",
            content_desc=el.get("content-desc", "") or "",
            resource_id=el.get("resource-id", "") or "",
            package=el.get("package", "") or "",
            bounds=_bounds(el.get("bounds")),
            clickable=_bool(el.get("clickable")),
            enabled=_bool(el.get("enabled")),
            focused=_bool(el.get("focused")),
            xpath_abs=xp,
            parent=parent,
        )
        flat.append(node)
        if parent is not None:
            parent.children.append(node)
        for child_el in el:
            walk(child_el, node, xp)
        return node

    # Use first top-level node as tree root; synthesize a virtual parent path of "".
    root = walk(top_nodes[0], None, "")
    # If multiple top-level nodes exist (rare), attach as siblings under a synthetic root.
    for extra in top_nodes[1:]:
        walk(extra, root.parent, "")
    return root, flat
