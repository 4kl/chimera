"""State-machine models for UI navigation.

A *state* is a semantic screen identity ("chat_screen", "main_page") that
many distinct screen fingerprints can map to. States are identified by
*features* extracted from the live UI (resource-ids, content descriptions,
class multiplicities). *Transitions* record that performing a semantic
action in one state typically lands you in another state."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


# Sentinel state names the framework reserves.
STATE_UNKNOWN = "unknown"
STATE_LAUNCHER = "launcher"


@dataclass
class StateFeature:
    """One identifying feature of a state.

    Kinds:
      - "resource_id"   : any node with resource-id == value is present
      - "content_desc"  : any node with content-desc == value is present
      - "text_contains" : any node whose text contains value (case-insensitive)
      - "class_min"     : at least `count` nodes of class == cls exist
                          (stored as value="<class>:<count>")
    """
    kind: str
    value: str
    weight: float = 1.0
    required: bool = False   # if True, state cannot match without this feature

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value,
                "weight": self.weight, "required": self.required}

    @classmethod
    def from_dict(cls, d: dict) -> "StateFeature":
        return cls(kind=d["kind"], value=d["value"],
                   weight=float(d.get("weight", 1.0)),
                   required=bool(d.get("required", False)))


@dataclass
class UIState:
    name: str                               # "main_page", "chat_screen", ...
    app_package: str
    app_version: str = ""
    features: list[StateFeature] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)  # known screen fps
    allowed_roles: list[str] = field(default_factory=list)  # semantic roles usable here
    confidence: float = 0.5                 # accumulated over observations
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def merge_observation(self, fp: str, roles: list[str] | None = None):
        """Called when we've confirmed this state was seen again."""
        if fp and fp not in self.fingerprints:
            self.fingerprints.append(fp)
        if roles:
            for r in roles:
                if r and r not in self.allowed_roles:
                    self.allowed_roles.append(r)
        self.last_seen = time.time()
        # Gentle confidence growth toward 1.0, capped.
        self.confidence = min(1.0, self.confidence + 0.05)


@dataclass
class StateTransition:
    from_state: str
    to_state: str
    role: str           # semantic role of the element acted on (e.g. "search_icon")
    action: str         # tap | type | swipe | back | launch
    app_package: str
    app_version: str = ""
    success: int = 0
    failure: int = 0
    last_ok: float = 0.0

    @property
    def confidence(self) -> float:
        total = self.success + self.failure
        if total == 0:
            return 0.5
        # Smooth toward 0.5 for low sample sizes (Laplace).
        return (self.success + 1) / (total + 2)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.from_state, self.role, self.action, self.to_state)
