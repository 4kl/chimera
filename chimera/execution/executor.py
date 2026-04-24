from __future__ import annotations

import logging
import time
from typing import Optional

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
from ..state.detector import StateDetector
from ..state.models import StateTransition
from ..state.navigator import Navigator
from ..state.store import StateStore
from .actions import perform

log = logging.getLogger("chimera.exec")


class Executor:
    def __init__(self,
                 driver: Driver,
                 memory: Memory,
                 gen: SelectorGenerator,
                 ollama: Ollama,
                 healer,
                 learning: LearningEngine,
                 state_detector: Optional[StateDetector] = None,
                 state_store: Optional[StateStore] = None,
                 navigator: Optional[Navigator] = None):
        self.d = driver
        self.mem = memory
        self.gen = gen
        self.llm = ollama
        self.heal = healer
        self.learning = learning
        self.state_detector = state_detector
        self.state_store = state_store
        self.nav = navigator

    # ---------- perception ----------
    def perceive(self, with_screenshot: bool = False) -> Frame:
        snap = capture(self.d, with_screenshot=with_screenshot)
        root, flat = parse(snap["xml"])
        fp = screen_fingerprint(flat, snap["package"])
        return Frame(
            root=root, flat=flat, fp=fp,
            package=snap["package"], activity=snap["activity"],
            png=snap.get("png"), ts=snap["ts"],
            raw_xml=snap["xml"],
        )

    # ---------- per-step entrypoint ----------
    def run_step(self, step: ActionStep, ctx: RunCtx):
        # Direct, no-selector actions.
        if step.action == "launch":
            # Prefer the orchestrator-resolved package (ctx.app). step.value
            # may be a common name like "youtube" that u2 cannot dispatch.
            pkg = (ctx.app or "").strip() or (step.value or "").strip()
            if not pkg:
                raise ExecutionError(
                    "launch step has no target package (ctx.app and step.value both empty)")
            if "." not in pkg:
                raise ExecutionError(
                    f"launch target {pkg!r} is not a package id; "
                    f"add it to APP_ALIAS or pass app_hint explicitly")
            log.info("launching %s", pkg)
            self.d.launch(pkg)
            self._wait_app_ready(pkg)
            return
        if step.action == "back":
            self.d.press("back")
            self._wait_ui_settle()
            return

        # State-aware pre-check: if the step declares a target_state and we're
        # elsewhere, navigate there first.
        self._navigate_if_needed(step, ctx)

        # Snapshot state before the action so we can record a transition
        # after it lands. This is how the state graph builds itself.
        pre_state = self._detect_state(ctx)

        # Element-based steps: inner retry covers the "UI is still loading
        # after a screen transition" case. Each attempt re-perceives, so the
        # LLM gets a fresh tree each time.
        attempts = 3
        last_err: ExecutionError | None = None
        for i in range(attempts):
            try:
                self._run_element_step(step, ctx)
                # Let the action's effect render before the next step perceives.
                self._wait_ui_settle(timeout=2.0, stable_ms=300)
                self._record_transition(step, ctx, pre_state, success=True)
                return
            except ExecutionError as e:
                last_err = e
                if i < attempts - 1:
                    wait = 1.0 * (2 ** i)  # 1s, 2s
                    log.info("role=%s not yet actionable (%s); "
                             "waiting %.1fs and re-perceiving",
                             step.role, e, wait)
                    time.sleep(wait)
        assert last_err is not None
        self._record_transition(step, ctx, pre_state, success=False)
        raise last_err

    def _run_element_step(self, step: ActionStep, ctx: RunCtx):
        frame = self.perceive()
        decision = self.learning.decide(ctx, frame, step.role)

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

    # ---------- state-awareness ----------
    def _detect_state(self, ctx: RunCtx) -> Optional[str]:
        if not self.state_detector or not ctx.app:
            return None
        try:
            frame = self.perceive()
            res = self.state_detector.detect(
                frame, ctx.app, ctx.app_version or "")
            return res.state.name
        except Exception as e:
            log.debug("state detect failed: %s", e)
            return None

    def _navigate_if_needed(self, step: ActionStep, ctx: RunCtx):
        if not step.target_state or not self.nav:
            return
        current = self._detect_state(ctx)
        if current == step.target_state:
            return
        log.info("state mismatch: current=%s target=%s; navigating",
                 current, step.target_state)
        self.nav.navigate_to(
            step.target_state, ctx,
            perceive=self.perceive,
            execute_step=self.run_step,
        )

    def _record_transition(self, step: ActionStep, ctx: RunCtx,
                           pre_state: Optional[str], success: bool):
        if not self.state_store or not ctx.app or not pre_state:
            return
        post_state = self._detect_state(ctx)
        if not post_state or post_state == pre_state and not success:
            return  # don't record self-loops on pure failures
        try:
            self.state_store.record_transition(
                StateTransition(
                    from_state=pre_state,
                    to_state=post_state or pre_state,
                    role=step.role, action=step.action,
                    app_package=ctx.app, app_version=ctx.app_version or ""),
                success=success,
            )
        except Exception as e:
            log.debug("record_transition failed: %s", e)

    # ---------- readiness waits ----------
    def _wait_app_ready(self, pkg: str, timeout: float = 8.0,
                        stable_ms: int = 500):
        """Poll until (a) the foreground package is `pkg` and (b) the screen
        fingerprint has stopped changing for `stable_ms`. Falls through
        silently on timeout — subsequent step retries will handle it."""
        deadline = time.time() + timeout
        last_fp: str | None = None
        stable_since: float | None = None
        while time.time() < deadline:
            try:
                cur = self.d.current_app().get("package", "")
            except Exception:
                cur = ""
            if cur != pkg:
                time.sleep(0.25)
                continue
            try:
                frame = self.perceive()
            except Exception:
                time.sleep(0.25)
                continue
            if frame.fp != last_fp:
                last_fp = frame.fp
                stable_since = time.time()
            elif stable_since is not None and \
                    (time.time() - stable_since) * 1000 >= stable_ms:
                log.debug("app %s ready (fp=%s)", pkg, frame.fp)
                return
            time.sleep(0.25)
        log.warning("wait_app_ready timed out for %s (last_fp=%s)", pkg, last_fp)

    def _wait_ui_settle(self, timeout: float = 3.0, stable_ms: int = 400):
        """Generic UI settle after an action that causes a transition."""
        deadline = time.time() + timeout
        last_fp: str | None = None
        stable_since: float | None = None
        while time.time() < deadline:
            try:
                frame = self.perceive()
            except Exception:
                time.sleep(0.2); continue
            if frame.fp != last_fp:
                last_fp = frame.fp
                stable_since = time.time()
            elif stable_since is not None and \
                    (time.time() - stable_since) * 1000 >= stable_ms:
                return
            time.sleep(0.2)

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
