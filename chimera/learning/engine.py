"""Learning Engine: decides per-role whether we execute from cache (fast path),
migrate from a prior version (tentative), learn from scratch (LLM-driven
discovery), or heal after a failure. Also tracks decay-adjusted confidence so
stale selectors don't pretend to be trustworthy."""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from typing import Optional

from ..core.models import Frame, RunCtx, SelectorBundle
from ..memory.store import Memory, decayed_score

log = logging.getLogger("chimera.learning")


class StepMode(enum.Enum):
    REUSE = "reuse"          # cached, fresh, trusted
    REVALIDATE = "revalidate"  # cached but stale/migrated → try, but heal if broken
    LEARN = "learn"          # no cache → LLM discovery
    HEAL = "heal"            # cached selector failed at runtime


@dataclass
class ModeDecision:
    mode: StepMode
    bundle: Optional[SelectorBundle]
    effective_confidence: float
    reason: str


# Below this, we don't trust the cached selector as-is.
MIN_REUSE_CONFIDENCE = 0.55


class LearningEngine:
    def __init__(self, memory: Memory):
        self.mem = memory

    def decide(self, ctx: RunCtx, frame: Frame, role: str) -> ModeDecision:
        """Called once per step BEFORE execution. Determines the execution
        mode and returns any pre-existing bundle to try."""
        app = ctx.app or frame.package
        version = ctx.app_version or ""

        # Record that we've seen this screen under this version (for future
        # version-similarity comparisons).
        if app and version:
            self.mem.record_screen(app, version, frame.fp)

        bundle = self.mem.get(app, version, frame.fp, role)
        if bundle is None:
            return ModeDecision(StepMode.LEARN, None, 0.0,
                                "no cached bundle for (app, version, screen, role)")

        effective = decayed_score(bundle.primary.score, bundle.last_ok,
                                  bundle.failures)

        if bundle.is_migrated:
            return ModeDecision(StepMode.REVALIDATE, bundle, effective,
                                f"migrated from another version; will validate")

        if effective < MIN_REUSE_CONFIDENCE:
            return ModeDecision(StepMode.REVALIDATE, bundle, effective,
                                f"confidence decayed to {effective:.2f}")

        return ModeDecision(StepMode.REUSE, bundle, effective,
                            f"fresh cache hit (conf={effective:.2f})")

    # ---------- lazy migration on first sight of a new version ----------
    def warm_up_version(self, app: str, new_version: str,
                        min_jaccard: float = 0.5) -> int:
        """On first encounter of a version, if it shares screens with a prior
        version, seed this version's selector table from the older one.

        Returns the number of roles copied."""
        if not app or not new_version:
            return 0
        if self.mem.known_screens(app, new_version):
            # we've seen screens on this version before → already warmed
            return 0
        prior = self.mem.find_similar_version(app, new_version, min_jaccard)
        if not prior:
            return 0
        jaccard = self.mem.version_similarity(app, new_version, prior)
        copied = self.mem.migrate_from(app, prior, new_version, jaccard)
        log.info("version warm-up: %s@%s ← %s@%s (jaccard=%.2f, roles=%d)",
                 app, new_version, app, prior, jaccard, copied)
        return copied

    # ---------- called by executor to update session counters ----------
    def note(self, ctx: RunCtx, role: str, mode: StepMode,
             bundle: Optional[SelectorBundle], outcome: str,
             reason: str = ""):
        s = ctx.session
        if s is None:
            return
        app = ctx.app or (bundle.app_package if bundle else "")
        version = ctx.app_version or (bundle.app_version if bundle else "")
        expr = bundle.primary.expr if bundle else ""

        if outcome == "ok" and mode == StepMode.REUSE:
            s.reused.append(role)
            self.mem.log(app, version, role, "ok", expr, reason)
        elif outcome == "ok" and mode in (StepMode.LEARN,):
            s.learned.append(role)
            self.mem.log(app, version, role, "learned", expr, reason)
        elif outcome == "ok" and mode in (StepMode.REVALIDATE,):
            s.reused.append(role)
            self.mem.log(app, version, role,
                         "migrated" if bundle and bundle.is_migrated else "ok",
                         expr, reason)
        elif outcome == "healed":
            s.healed.append(role)
            self.mem.log(app, version, role, "healed", expr, reason)
        elif outcome == "migrated":
            s.migrated.append(role)
            self.mem.log(app, version, role, "migrated", expr, reason)
        elif outcome.startswith("fail"):
            s.failures.append((role, reason))
            self.mem.log(app, version, role, outcome, expr, reason)
