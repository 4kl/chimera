"""Executes a planned path through the state graph.

The navigator is the glue between the state subsystem and the rest of the
framework. It doesn't know how to tap/type — for that it delegates back to
the executor. It only decides *what* semantic step to perform next to reach
the target state and validates the outcome.

Recovery policy:
  - After each hop, re-detect the current state.
  - If it matches the expected next state → continue, record success.
  - If not → record failure on the transition, then re-plan from the new
    current state. Bail after MAX_RECOVERIES.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from ..core.errors import ExecutionError
from ..core.models import ActionStep, RunCtx
from .detector import StateDetector, DetectionResult
from .graph import StateGraph
from .models import STATE_UNKNOWN, StateTransition
from .planner import StatePlanner
from .store import StateStore

log = logging.getLogger("chimera.nav")

MAX_RECOVERIES = 2
MAX_TOTAL_HOPS = 8


class Navigator:
    def __init__(self, detector: StateDetector, store: StateStore):
        self.detector = detector
        self.store = store

    def navigate_to(self, target_state: str, ctx: RunCtx,
                    perceive: Callable,
                    execute_step: Callable[[ActionStep, RunCtx], None],
                    ) -> DetectionResult:
        """Navigate from the current state to `target_state`. `perceive` is
        a zero-arg callable returning a Frame; `execute_step` runs an
        ActionStep through the element-executor (so each navigation hop
        reuses the selector cache/heal stack exactly like any other step)."""
        app = ctx.app or ""
        version = ctx.app_version or ""
        if not app or not target_state:
            return DetectionResult(  # type: ignore[return-value]
                state=None, score=0.0, source="noop")  # caller handles

        frame = perceive()
        current = self.detector.detect(frame, app, version).state

        if current.name == target_state:
            return DetectionResult(current, 1.0, "already_here")

        recoveries = 0
        total_hops = 0
        while current.name != target_state and total_hops < MAX_TOTAL_HOPS:
            graph = StateGraph.load(self.store, app, version)
            planner = StatePlanner(graph)
            path = planner.plan(current.name, target_state)

            if not path:
                # Escape hatch: if we have no edges out of `current`, try
                # pressing Back and see if we land somewhere known.
                if current.name == STATE_UNKNOWN or \
                        not graph.edges_from(current.name):
                    log.info("no known path from %s → %s; trying back",
                             current.name, target_state)
                    self._press_back(ctx, execute_step)
                    frame = perceive()
                    current = self.detector.detect(frame, app, version).state
                    total_hops += 1
                    recoveries += 1
                    if recoveries > MAX_RECOVERIES:
                        raise ExecutionError(
                            f"navigator: cannot reach {target_state!r} "
                            f"from {current.name!r} (no viable path)")
                    continue
                raise ExecutionError(
                    f"navigator: no path from {current.name!r} to "
                    f"{target_state!r}")

            edge = path[0]
            log.info("nav hop: %s --[%s:%s]--> %s (conf=%.2f)",
                     edge.from_state, edge.role, edge.action, edge.to_state,
                     edge.confidence)
            step = ActionStep(role=edge.role, action=edge.action,
                              description=f"navigate to {edge.to_state}")
            try:
                execute_step(step, ctx)
            except Exception as e:
                self.store.record_transition(
                    StateTransition(
                        from_state=edge.from_state, to_state=edge.to_state,
                        role=edge.role, action=edge.action,
                        app_package=app, app_version=version),
                    success=False)
                log.warning("nav hop failed (%s); recovering", e)
                recoveries += 1
                if recoveries > MAX_RECOVERIES:
                    raise ExecutionError(
                        f"navigator: hop {edge.role!r} failed too many times"
                    ) from e
                frame = perceive()
                current = self.detector.detect(frame, app, version).state
                total_hops += 1
                continue

            frame = perceive()
            observed = self.detector.detect(frame, app, version).state
            expected = edge.to_state
            succeeded = observed.name == expected

            self.store.record_transition(
                StateTransition(
                    from_state=edge.from_state, to_state=observed.name,
                    role=edge.role, action=edge.action,
                    app_package=app, app_version=version),
                success=succeeded)

            if not succeeded:
                log.info("nav hop landed in %s (expected %s); re-planning",
                         observed.name, expected)
                recoveries += 1
                if recoveries > MAX_RECOVERIES:
                    raise ExecutionError(
                        f"navigator: overshot target state too many times "
                        f"(landed in {observed.name!r})")

            current = observed
            total_hops += 1

        if current.name != target_state:
            raise ExecutionError(
                f"navigator: exhausted hops without reaching "
                f"{target_state!r} (stuck at {current.name!r})")

        return DetectionResult(current, 1.0, "navigated")

    def _press_back(self, ctx: RunCtx,
                    execute_step: Callable[[ActionStep, RunCtx], None]):
        execute_step(ActionStep(role="back_button", action="back",
                                description="press back"), ctx)
