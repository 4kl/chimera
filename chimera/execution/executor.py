from __future__ import annotations

import logging
import time

from ..core.driver import Driver
from ..core.errors import ExecutionError
from ..core.models import (ActionStep, Frame, RunCtx, SelectorBundle,
                           SelectorCandidate)
from ..learning.engine import LearningEngine, ModeDecision, StepMode
from ..memory.store import Memory
from ..perception.capture import capture
from ..perception.fingerprint import screen_fingerprint
from ..perception.parser import parse
from ..reasoning.matcher import llm_pick
from ..reasoning.ollama_client import Ollama
from ..selector.generator import SelectorGenerator
from ..selector.validator import validate
from .actions import perform

log = logging.getLogger("chimera.exec")


class Executor:
    def __init__(self,
                 driver: Driver,
                 memory: Memory,
                 gen: SelectorGenerator,
                 ollama: Ollama,
                 healer,
                 learning: LearningEngine):
        self.d = driver
        self.mem = memory
        self.gen = gen
        self.llm = ollama
        self.heal = healer
        self.learning = learning

    # ---------- perception ----------
    def perceive(self, with_screenshot: bool = False) -> Frame:
        snap = capture(self.d, with_screenshot=with_screenshot)
        root, flat = parse(snap["xml"])
        fp = screen_fingerprint(flat, snap["package"])
        return Frame(
            root=root, flat=flat, fp=fp,
            package=snap["package"], activity=snap["activity"],
            png=snap.get("png"), ts=snap["ts"],
        )

    # ---------- per-step entrypoint ----------
    def run_step(self, step: ActionStep, ctx: RunCtx):
        # No-selector steps execute without perception.
        if step.action in ("launch", "back"):
            if not perform(self.d, SelectorCandidate("", "none", 0.0), step):
                raise ExecutionError(f"action {step.action} failed ({step.value!r})")
            time.sleep(1.0)
            return

        frame = self.perceive()
        decision = self.learning.decide(ctx, frame, step.role)
        app = ctx.app or frame.package

        log.info("role=%s mode=%s conf=%.2f reason=%s",
                 step.role, decision.mode.value,
                 decision.effective_confidence, decision.reason)

        if decision.mode in (StepMode.REUSE, StepMode.REVALIDATE) and decision.bundle:
            if self._try_bundle(decision.bundle, step, frame, ctx):
                self.learning.note(ctx, step.role, decision.mode,
                                   decision.bundle, "ok",
                                   reason=decision.reason)
                return
            # Cache failed → heal path
            decision = ModeDecision(StepMode.HEAL, decision.bundle,
                                    decision.effective_confidence,
                                    "primary+fallbacks failed")
            self.mem.bump_failure(decision.bundle)

        # LEARN or HEAL → discover via LLM on current frame
        new_bundle = self._discover_and_bind(step, frame, ctx,
                                             prior=decision.bundle)
        if not perform(self.d, new_bundle.primary, step):
            if new_bundle.fallbacks and perform(
                    self.d, new_bundle.fallbacks[0], step):
                new_bundle.primary, new_bundle.fallbacks[0] = (
                    new_bundle.fallbacks[0], new_bundle.primary)
            else:
                self.learning.note(ctx, step.role, decision.mode,
                                   new_bundle, "fail_all",
                                   reason="action failed after discovery")
                raise ExecutionError(
                    f"cannot perform {step.action} on role={step.role!r}")

        new_bundle.last_ok = time.time()
        new_bundle.failures = 0
        self.mem.put(new_bundle)

        outcome = "healed" if decision.mode == StepMode.HEAL else "ok"
        self.learning.note(ctx, step.role,
                           StepMode.HEAL if decision.mode == StepMode.HEAL else StepMode.LEARN,
                           new_bundle, outcome,
                           reason=new_bundle.description)

    # ---------- helpers ----------
    def _try_bundle(self, b: SelectorBundle, step: ActionStep,
                    frame: Frame, ctx: RunCtx) -> bool:
        app = ctx.app or frame.package
        for cand in b.all_candidates():
            if not validate(self.d, cand, b.element_fingerprint):
                continue
            if perform(self.d, cand, step):
                # Promote working fallback to primary
                if cand is not b.primary:
                    b.fallbacks = [c for c in b.all_candidates() if c is not cand]
                    b.primary = cand
                    b.version += 1
                b.last_ok = time.time()
                b.failures = 0
                # Migrated bundles get re-parented under the current version
                # and promoted from 'migrated' to 'learned' after first success.
                b.app_version = ctx.app_version or b.app_version
                b.screen_fingerprint = frame.fp
                if b.primary.provenance == "migrated":
                    for c in b.all_candidates():
                        c.provenance = "learned"
                self.mem.put(b)
                return True
            self.mem.log(app, b.app_version, step.role,
                         "fail_primary", cand.expr)
        return False

    def _discover_and_bind(self, step: ActionStep, frame: Frame,
                           ctx: RunCtx, prior: SelectorBundle | None
                           ) -> SelectorBundle:
        desc = step.description or (prior.description if prior else step.role)
        pick = llm_pick(self.llm, step.role, desc, frame.flat)

        if pick["index"] < 0 or pick["index"] >= len(frame.flat):
            if prior is not None:
                return self.heal.heal(prior, frame, self.llm,
                                      self.gen, self.mem, desc)
            raise ExecutionError(
                f"LLM could not locate role={step.role!r}: {pick.get('reason','')}")

        node = frame.flat[pick["index"]]
        cands = self.gen.generate(node, frame.flat)
        provenance = "healed" if prior else "learned"
        for c in cands:
            c.provenance = provenance

        return SelectorBundle(
            primary=cands[0],
            fallbacks=cands[1:4],
            semantic_role=step.role,
            app_package=ctx.app or frame.package,
            app_version=ctx.app_version,
            screen_fingerprint=frame.fp,
            element_fingerprint=node.semantic_key(),
            description=desc,
            version=(prior.version + 1) if prior else 1,
        )
