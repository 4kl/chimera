"""Class-level declarative state objects à la PUMA.

Users write:

    class WhatsApp(AppGraph, package="com.whatsapp"):
        conversations_state = DeclaredState(
            name="conversations",
            xpaths=[CONVERSATIONS_LOGO, CONVERSATIONS_NEW_CHAT],
            initial=True,
        )
        chat_state = DeclaredState(
            name="chat",
            xpaths=[CHAT_HEADER, CHAT_INPUT],
            parent=conversations_state,
            validator=lambda driver, conversation=None: (
                conversation is None
                or driver.is_present(f"//*[@text='{conversation}']")),
        )
        conversations_state.to(chat_state, via=go_to_chat)

At AppGraph.__init__ time these declarations are reflected into Chimera's
`StateStore`: features and seed transitions are written, so the state
detector and navigator recognize them without any prior runs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..state.models import StateFeature


@dataclass
class DeclaredTransition:
    target: "DeclaredState"
    via: Callable   # (driver, **kwargs) -> None
    name: str = ""


class DeclaredState:
    def __init__(self,
                 name: str,
                 xpaths: Optional[list[str]] = None,
                 features: Optional[list[StateFeature]] = None,
                 parent: Optional["DeclaredState"] = None,
                 initial: bool = False,
                 validator: Optional[Callable] = None):
        self.name = name
        self.xpaths = list(xpaths or [])
        self._explicit_features = list(features or [])
        self.parent = parent
        self.initial = initial
        self.validator = validator
        self._outbound: list[DeclaredTransition] = []

    # -------- class-body-time API --------
    def to(self, target: "DeclaredState", via: Callable,
           name: Optional[str] = None) -> "DeclaredState":
        """Declare a transition. `via` receives the XPathDriver as first arg
        plus whatever keyword args the caller wants to forward."""
        self._outbound.append(
            DeclaredTransition(target=target, via=via,
                               name=name or via.__name__))
        return self

    # -------- runtime --------
    def features(self) -> list[StateFeature]:
        """Features used by the state detector to score this state against a
        live frame. Explicit features win; XPaths are translated best-effort
        and stored as xpath_present as a last resort."""
        if self._explicit_features:
            return list(self._explicit_features)
        out: list[StateFeature] = []
        for xp in self.xpaths:
            out.extend(xpath_to_features(xp))
        return out

    def outbound(self) -> list[DeclaredTransition]:
        return list(self._outbound)

    def __repr__(self):
        return f"DeclaredState({self.name!r})"


# ---- XPath → StateFeature translator ------------------------------------
_RID_RE = re.compile(r"@resource-id\s*=\s*['\"]([^'\"]+)['\"]")
_DESC_RE = re.compile(r"@content-desc\s*=\s*['\"]([^'\"]+)['\"]")
_TEXT_RE = re.compile(r"@text\s*=\s*['\"]([^'\"]+)['\"]")
_CONTAINS_TEXT_RE = re.compile(
    r"contains\(\s*@text\s*,\s*['\"]([^'\"]+)['\"]\s*\)")


def xpath_to_features(xpath: str) -> list[StateFeature]:
    """Best-effort translation of a simple XPath into one or more
    StateFeatures. For common single-predicate XPaths we produce a precise
    feature (resource_id / content_desc / text_contains); anything beyond
    that falls back to `xpath_present` (evaluated against the raw XML on
    the detector side)."""
    if not xpath:
        return []
    feats: list[StateFeature] = []
    rid = _RID_RE.search(xpath)
    desc = _DESC_RE.search(xpath)
    text = _TEXT_RE.search(xpath)
    contains_text = _CONTAINS_TEXT_RE.search(xpath)

    added_any = False
    # Only treat as "simple" if exactly ONE predicate appears.
    predicates = sum(bool(m) for m in (rid, desc, text, contains_text))
    if predicates == 1:
        if rid:
            feats.append(StateFeature("resource_id", rid.group(1),
                                      weight=1.0, required=True))
            added_any = True
        elif desc:
            feats.append(StateFeature("content_desc", desc.group(1),
                                      weight=1.0, required=True))
            added_any = True
        elif text:
            feats.append(StateFeature("text_contains", text.group(1),
                                      weight=1.0, required=True))
            added_any = True
        elif contains_text:
            feats.append(StateFeature("text_contains",
                                      contains_text.group(1),
                                      weight=1.0, required=True))
            added_any = True

    if not added_any:
        feats.append(StateFeature("xpath_present", xpath,
                                  weight=1.0, required=True))
    return feats
