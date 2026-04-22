from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UINode:
    index: int
    cls: str
    text: str
    content_desc: str
    resource_id: str
    package: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    enabled: bool
    focused: bool
    xpath_abs: str
    parent: Optional["UINode"] = None
    children: list["UINode"] = field(default_factory=list)

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def semantic_key(self) -> str:
        raw = f"{self.resource_id}|{self.content_desc}|{self.text[:32]}|{self.cls}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def to_slim(self) -> dict:
        return {
            "i": self.index,
            "cls": self.cls,
            "text": self.text[:60],
            "desc": self.content_desc[:60],
            "rid": self.resource_id,
            "clickable": self.clickable,
            "bounds": list(self.bounds),
        }


@dataclass
class SelectorCandidate:
    expr: str
    strategy: str
    score: float
    provenance: str = "generated"  # generated | llm | healed

    def to_dict(self) -> dict:
        return {"expr": self.expr, "strategy": self.strategy,
                "score": self.score, "provenance": self.provenance}

    @classmethod
    def from_dict(cls, d: dict) -> "SelectorCandidate":
        return cls(expr=d["expr"], strategy=d["strategy"],
                   score=d["score"], provenance=d.get("provenance", "generated"))


@dataclass
class SelectorBundle:
    primary: SelectorCandidate
    fallbacks: list[SelectorCandidate]
    semantic_role: str
    app_package: str
    screen_fingerprint: str
    element_fingerprint: str
    app_version: str = ""
    description: str = ""
    last_ok: float = 0.0
    failures: int = 0
    version: int = 1  # bundle revision, not app version
    # Set by Memory.get when this bundle was served from a different screen
    # fingerprint than was requested (cross-screen reuse). None otherwise.
    _origin_screen_fp: Optional[str] = None

    def all_candidates(self) -> list[SelectorCandidate]:
        return [self.primary, *self.fallbacks]

    @property
    def is_migrated(self) -> bool:
        return self.primary.provenance == "migrated"


@dataclass
class ActionStep:
    role: str
    action: str  # tap | type | swipe | wait | back | launch
    value: Optional[str] = None
    description: str = ""


@dataclass
class Intent:
    raw: str
    app_hint: Optional[str]
    steps: list[ActionStep]


@dataclass
class RunCtx:
    app: Optional[str]
    app_version: str = ""
    started_at: float = field(default_factory=time.time)
    session: Optional["Session"] = None


@dataclass
class Session:
    """Per-run metrics: what was learned, reused, healed, migrated."""
    started_at: float = field(default_factory=time.time)
    learned: list[str] = field(default_factory=list)   # roles newly learned
    reused: list[str] = field(default_factory=list)    # roles served from cache
    healed: list[str] = field(default_factory=list)    # roles re-bound after failure
    migrated: list[str] = field(default_factory=list)  # roles seeded from prior version
    failures: list[tuple[str, str]] = field(default_factory=list)  # (role, reason)

    def summary(self) -> dict:
        return {
            "duration_s": round(time.time() - self.started_at, 2),
            "reused": len(self.reused),
            "learned": len(self.learned),
            "healed": len(self.healed),
            "migrated": len(self.migrated),
            "failures": len(self.failures),
            "detail": {
                "reused": self.reused, "learned": self.learned,
                "healed": self.healed, "migrated": self.migrated,
                "failures": self.failures,
            },
        }


@dataclass
class Frame:
    """A perceived snapshot of the current screen."""
    root: UINode
    flat: list[UINode]
    fp: str
    package: str
    activity: str
    png: Optional[bytes] = None
    ts: float = field(default_factory=time.time)
