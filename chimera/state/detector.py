"""State detection: given a captured Frame, classify which semantic state
we're in.

Strategy:
  1. Fast path — direct fingerprint hit: if the current screen_fp has been
     seen in any known state, return that state (almost-free).
  2. Structural path — score the frame against each known state's features;
     if the top state scores ≥ `match_threshold` with a clear lead over #2,
     return it.
  3. LLM path — ask the model to classify among known-state names or propose
     a new snake_case name; extract features from the frame; persist a new
     UIState on first sight.

The detector is side-effectful by design: it grows the state catalogue as
navigation happens. That's the whole point — the system starts with zero
states and builds its own map."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..core.models import Frame, UINode
from ..reasoning.matcher import llm_classify_state
from ..reasoning.ollama_client import Ollama
from .models import (STATE_LAUNCHER, STATE_UNKNOWN, StateFeature, UIState)
from .store import StateStore

log = logging.getLogger("chimera.state")


@dataclass
class DetectionResult:
    state: UIState
    score: float
    source: str   # "fingerprint" | "features" | "llm" | "new"


# Minimum score for a features-based match to be accepted without LLM.
MATCH_THRESHOLD = 0.60
# Minimum gap between #1 and #2 for features-based matching to be unambiguous.
MATCH_GAP = 0.15


class StateDetector:
    def __init__(self, store: StateStore, ollama: Ollama):
        self.store = store
        self.llm = ollama

    # ---------------- public ----------------
    def detect(self, frame: Frame, app: str, version: str,
               known_roles: Optional[list[str]] = None) -> DetectionResult:
        """Classify `frame` into a UIState. May call the LLM on unseen screens."""
        # Launcher carve-out — we never navigate through launcher states
        # semantically; treat it as a fixed sentinel.
        if self._is_launcher(frame, app):
            st = UIState(name=STATE_LAUNCHER, app_package=app, app_version=version)
            return DetectionResult(st, 1.0, "fingerprint")

        # 1) fingerprint-level cache
        cached = self.store.find_state_by_fingerprint(app, version, frame.fp)
        if cached is not None:
            cached.merge_observation(frame.fp, roles=known_roles)
            self.store.upsert_state(cached)
            return DetectionResult(cached, 1.0, "fingerprint")

        # 2) feature-scored match against known states for this (app, version)
        candidates = self.store.list_states(app, version)
        # If nothing under this exact version, borrow priors from other
        # versions (helpful after app updates).
        if not candidates:
            candidates = [s for s in self.store.all_versions_states(app)
                          if s.name not in {STATE_UNKNOWN, STATE_LAUNCHER}]

        scored = sorted(
            ((s, _match_score(frame, s)) for s in candidates),
            key=lambda x: -x[1],
        )
        if scored:
            top, top_score = scored[0]
            second = scored[1][1] if len(scored) > 1 else 0.0
            if top_score >= MATCH_THRESHOLD and (top_score - second) >= MATCH_GAP:
                top.merge_observation(frame.fp, roles=known_roles)
                # re-parent under the current version if borrowed
                top.app_version = version
                self.store.upsert_state(top)
                return DetectionResult(top, top_score, "features")

        # 3) LLM classification (proposes reuse of known name or a new one)
        known_names = [s.name for s in candidates]
        try:
            verdict = llm_classify_state(self.llm, frame, known_names)
        except Exception as e:
            log.warning("LLM state classification failed: %s", e)
            verdict = None

        if verdict is None:
            unknown = UIState(name=STATE_UNKNOWN,
                              app_package=app, app_version=version,
                              fingerprints=[frame.fp],
                              allowed_roles=list(known_roles or []))
            # Don't persist 'unknown' — it's a sentinel, not a learned state.
            return DetectionResult(unknown, 0.0, "new")

        name = _sanitize_name(verdict.get("state") or STATE_UNKNOWN)

        # Reuse an existing state by name if the LLM picked one
        existing = self.store.get_state(app, version, name)
        if existing is None and candidates:
            # Also check cross-version same name so we inherit features
            for s in self.store.all_versions_states(app):
                if s.name == name:
                    existing = UIState(
                        name=name, app_package=app, app_version=version,
                        features=list(s.features),
                        fingerprints=[],
                        allowed_roles=list(s.allowed_roles),
                        confidence=max(0.3, s.confidence * 0.8),
                    )
                    break

        if existing is None:
            # Brand-new state — seed features from what the LLM pointed at.
            features = _features_from_verdict(frame, verdict)
            if not features:
                features = _auto_features(frame)
            existing = UIState(
                name=name, app_package=app, app_version=version,
                features=features,
                fingerprints=[],
                allowed_roles=list(known_roles or []),
                confidence=max(0.3, float(verdict.get("confidence", 0.5))),
            )

        existing.merge_observation(frame.fp, roles=known_roles)
        # Augment features the LLM highlighted on this seen instance.
        for f in _features_from_verdict(frame, verdict):
            if not any(ef.kind == f.kind and ef.value == f.value
                       for ef in existing.features):
                existing.features.append(f)
        self.store.upsert_state(existing)
        return DetectionResult(existing, float(verdict.get("confidence", 0.5)),
                               "llm")

    # ---------------- internals ----------------
    @staticmethod
    def _is_launcher(frame: Frame, app: str) -> bool:
        pkg = (frame.package or "").lower()
        return "launcher" in pkg or pkg.endswith(".nexuslauncher") \
            or pkg == "com.android.launcher" \
            or pkg == "com.sec.android.app.launcher" \
            or (app and pkg != app and "launcher" in pkg)


# ---------------- scoring ----------------
def _match_score(frame: Frame, state: UIState) -> float:
    """Weighted coverage of state features in the frame (0..1)."""
    if not state.features:
        return 0.0
    total_w = sum(f.weight for f in state.features) or 1.0
    got_w = 0.0
    for f in state.features:
        if _feature_present(frame.flat, f, frame.raw_xml):
            got_w += f.weight
        elif f.required:
            return 0.0
    return got_w / total_w


def _feature_present(flat: list[UINode], f: StateFeature,
                     raw_xml: str = "") -> bool:
    if f.kind == "resource_id":
        return any(n.resource_id == f.value for n in flat)
    if f.kind == "content_desc":
        return any(n.content_desc == f.value for n in flat)
    if f.kind == "text_contains":
        needle = f.value.lower()
        return any(needle in (n.text or "").lower()
                   for n in flat if n.text)
    if f.kind == "class_min":
        try:
            cls, min_count = f.value.rsplit(":", 1)
            return sum(1 for n in flat if n.cls == cls) >= int(min_count)
        except Exception:
            return False
    if f.kind == "xpath_present":
        # Evaluate against the raw a11y XML. This is the escape hatch for
        # declared states whose signatures are raw XPaths (PUMA-style).
        if not raw_xml:
            return False
        try:
            import lxml.etree as ET
            tree = ET.fromstring(raw_xml.encode())
            return bool(tree.xpath(f.value))
        except Exception:
            return False
    return False


# ---------------- feature extraction ----------------
def _auto_features(frame: Frame) -> list[StateFeature]:
    """Pick 3-5 stable resource-ids from the frame as a signature."""
    rids: dict[str, int] = {}
    for n in frame.flat:
        if n.resource_id:
            rids[n.resource_id] = rids.get(n.resource_id, 0) + 1
    # Prefer unique, app-scoped ids (skip android:id/*).
    ranked = sorted(
        (rid for rid in rids if not rid.startswith("android:id/")),
        key=lambda r: (rids[r], -len(r)),   # prefer few occurrences, longer ids
    )
    top = ranked[:4] or list(rids.keys())[:4]
    return [StateFeature(kind="resource_id", value=r,
                         weight=1.0, required=False) for r in top]


def _features_from_verdict(frame: Frame, verdict: dict) -> list[StateFeature]:
    """The LLM may return {'features': [{kind, value}, ...]}. Filter those
    that actually appear in the frame so we never store phantoms."""
    raw = verdict.get("features") or []
    out: list[StateFeature] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        kind = f.get("kind") or ""
        value = f.get("value") or ""
        if not kind or not value:
            continue
        feat = StateFeature(
            kind=str(kind), value=str(value),
            weight=float(f.get("weight", 1.0)),
            required=bool(f.get("required", False)),
        )
        if _feature_present(frame.flat, feat, frame.raw_xml):
            out.append(feat)
    return out


def _sanitize_name(name: str) -> str:
    import re
    n = name.strip().lower()
    n = re.sub(r"[^a-z0-9_]+", "_", n)
    n = re.sub(r"_+", "_", n).strip("_")
    return n or STATE_UNKNOWN
