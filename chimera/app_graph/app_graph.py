"""AppGraph — base class users subclass to declare an app's state machine.

Typical skeleton:

    class WhatsApp(AppGraph, package="com.whatsapp"):
        # --- declared states (class-level) ---
        conversations_state = DeclaredState(
            name="conversations",
            xpaths=[CONVERSATIONS_LOGO, ...],
            initial=True,
        )
        chat_state = DeclaredState(
            name="chat",
            xpaths=[CHAT_HEADER, CHAT_INPUT],
            parent=conversations_state,
        )
        # --- transitions ---
        conversations_state.to(chat_state, via=go_to_chat)

        @action(chat_state)
        def send_message(self, message_text, conversation=None):
            self.driver.send_keys(CHAT_INPUT, message_text)
            self.driver.click(SEND_BUTTON)

Usage:

    from chimera.orchestrator import Chimera
    ch = Chimera()
    app = WhatsApp(ch)
    app.send_message("hey", conversation="John")

The AppGraph does NOT own a driver of its own — it delegates to the Chimera
orchestrator so everything benefits from selector memory, version-aware
caching, LLM fallback, self-healing, and the state store."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..core.errors import ChimeraError, ExecutionError
from ..core.models import ActionStep, RunCtx, Session
from ..state.models import (STATE_UNKNOWN, StateFeature, StateTransition,
                            UIState)
from .declared_state import DeclaredState, DeclaredTransition
from .popup import PopupHandler
from .xpath_driver import XPathDriver, XPathNotFound

log = logging.getLogger("chimera.app_graph")


class AppGraphMeta(type):
    """Collects class-level DeclaredState attributes into cls._states on
    subclass creation. Preserves declaration order."""

    def __new__(mcs, name, bases, ns, **kwargs):
        pkg = kwargs.pop("package", None)
        cls = super().__new__(mcs, name, bases, ns)
        if pkg is not None:
            cls.package = pkg
        cls._states = [v for v in ns.values() if isinstance(v, DeclaredState)]
        return cls

    def __init__(cls, name, bases, ns, **kwargs):
        super().__init__(name, bases, ns)


def supported_version(version: str):
    """Class decorator: record the app versionName the declared XPaths were
    written for. Chimera uses this to detect drift and pre-warm the heal
    path the first time a different version is encountered."""
    def decorate(cls):
        cls.declared_version = version
        return cls
    return decorate


class AppGraph(metaclass=AppGraphMeta):
    package: str = ""
    declared_version: Optional[str] = None
    _states: list[DeclaredState] = []
    popup_handlers: list[PopupHandler] = []

    def __init__(self, chimera):
        if not self.package:
            raise ChimeraError("AppGraph subclass must set `package`.")
        self.chimera = chimera
        self.ctx = RunCtx(app=self.package, app_version="",
                          session=Session())
        # Resolve actual installed version now, not at method-call time.
        try:
            prof = chimera.profiler.of(self.package)
            self.ctx.app_version = prof.version or ""
        except Exception as e:
            log.debug("version resolve failed: %s", e)

        self.driver = XPathDriver(chimera, ctx_provider=lambda: self.ctx)
        self._sync_states_to_store()

    # -------------- state / navigation plumbing --------------
    def _sync_states_to_store(self):
        """Reflect the class-level declared states + transitions into
        Chimera's StateStore so the detector recognizes them without any
        prior runs having built the graph."""
        store = self.chimera.state_store
        pkg, ver = self.package, self.ctx.app_version

        # States
        for s in self._states:
            feats = s.features()
            ui = UIState(
                name=s.name,
                app_package=pkg,
                app_version=ver,
                features=feats,
                fingerprints=[],
                allowed_roles=[],
                confidence=0.8,   # pre-trusted; earned from static decl
            )
            store.upsert_state(ui)

        # Seed transitions: every declared edge gets a small success prior so
        # the Dijkstra planner prefers them. Runtime successes grow the
        # confidence further.
        for s in self._states:
            for t in s.outbound():
                seed = StateTransition(
                    from_state=s.name, to_state=t.target.name,
                    role=f"declared:{t.name}", action="composite",
                    app_package=pkg, app_version=ver,
                    success=3, failure=0, last_ok=time.time(),
                )
                store.record_transition(seed, success=True)

    def _ensure_launched(self):
        try:
            cur = self.chimera.d.current_app().get("package", "")
        except Exception:
            cur = ""
        if cur != self.package:
            log.info("launching %s", self.package)
            if cur:
                self.chimera.d.press("home")
                time.sleep(0.3)
            self.chimera.d.launch(self.package)
            self.chimera.exec._wait_app_ready(self.package)
            # Re-resolve version post-launch (first install may differ)
            self.chimera.profiler.invalidate(self.package)
            self.ctx.app_version = self.chimera.profiler.of(self.package).version

    def _detect_current(self) -> str:
        """Classify the current screen against our declared states first,
        then fall through to Chimera's detector for dynamic classification.
        """
        self._dismiss_popups()
        frame = self.chimera.exec.perceive()
        # Fast path: a declared state whose all-required features are present.
        best = None
        best_matched = 0
        for s in self._states:
            feats = s.features()
            if not feats:
                continue
            if all(self._feature_present(frame, f) for f in feats):
                if len(feats) > best_matched:
                    best = s.name
                    best_matched = len(feats)
        if best:
            return best
        # Generic detector (LLM-backed when necessary)
        res = self.chimera.state_detector.detect(
            frame, self.package, self.ctx.app_version or "")
        return res.state.name

    @staticmethod
    def _feature_present(frame, feature) -> bool:
        from ..state.detector import _feature_present as fp
        return fp(frame.flat, feature, frame.raw_xml)

    def _enter_state(self, target: DeclaredState,
                     context_kwargs: Optional[dict] = None):
        """Called by the @action wrapper: make sure we're in `target`
        (and, if the state has a validator, that its context matches)."""
        self._ensure_launched()
        current = self._detect_current()
        context_kwargs = context_kwargs or {}

        if current == target.name:
            if target.validator:
                try:
                    ok = target.validator(self.driver, **_filter_ctx(
                        target.validator, context_kwargs))
                except TypeError:
                    ok = target.validator(self.driver)
                if ok:
                    return
                # context mismatch: step out to the parent and re-enter.
                log.info("state %s context-invalid; going back to parent",
                         target.name)
                self.driver.back()
                current = self._detect_current()

        # Navigate via declared transitions first (deterministic, XPath-driven).
        if self._walk_declared_path(current, target, context_kwargs):
            return

        # Fall back to Chimera's dynamic Navigator (uses the same state store
        # we've seeded).
        log.info("declared path %s→%s unavailable; using dynamic navigator",
                 current, target.name)
        self.chimera.navigator.navigate_to(
            target.name, self.ctx,
            perceive=self.chimera.exec.perceive,
            execute_step=self.chimera.exec.run_step,
        )

    def _walk_declared_path(self, current_name: str,
                            target: DeclaredState,
                            ctx_kwargs: dict) -> bool:
        """BFS over the declared graph. Execute transitions in order. Returns
        True if we reached `target`, False if no declared path exists."""
        if current_name == target.name:
            return True
        from collections import deque
        name_to_state = {s.name: s for s in self._states}
        if current_name not in name_to_state:
            return False
        q = deque([(current_name, [])])
        seen = {current_name}
        while q:
            node, path = q.popleft()
            s = name_to_state[node]
            for t in s.outbound():
                if t.target.name == target.name:
                    for edge in [*path, t]:
                        self._execute_transition(edge, ctx_kwargs)
                    return True
                if t.target.name in seen:
                    continue
                seen.add(t.target.name)
                if t.target.name in name_to_state:
                    q.append((t.target.name, [*path, t]))
        return False

    def _execute_transition(self, t: DeclaredTransition, ctx_kwargs: dict):
        log.info("declared transition: → %s (via %s)",
                 t.target.name, t.name)
        try:
            # Forward only the kwargs the transition function accepts.
            kwargs = _filter_ctx(t.via, ctx_kwargs)
            t.via(self.driver, **kwargs)
        except XPathNotFound as e:
            # Repair path: the declared transition broke. We let the dynamic
            # navigator pick it up — it will use the graph + LLM.
            raise ExecutionError(
                f"declared transition to {t.target.name} broken: {e}") from e
        # allow UI to settle before the next hop
        self.chimera.exec._wait_ui_settle(timeout=2.0, stable_ms=300)

    def _expect_state(self, expected: DeclaredState,
                      previous: DeclaredState):
        cur = self._detect_current()
        store = self.chimera.state_store
        store.record_transition(
            StateTransition(
                from_state=previous.name, to_state=cur,
                role="action_end", action="composite",
                app_package=self.package,
                app_version=self.ctx.app_version or ""),
            success=(cur == expected.name),
        )
        if cur != expected.name:
            log.warning("expected to end in %s but observed %s",
                        expected.name, cur)

    # -------------- popups --------------
    def add_popup_handler(self, h: PopupHandler):
        self.popup_handlers = [*self.popup_handlers, h]

    def _dismiss_popups(self):
        for h in self.popup_handlers:
            try:
                if h.matches(self.driver):
                    log.info("dismissing popup %s", h.identifiers[0])
                    h.dismiss(self.driver)
            except Exception as e:
                log.debug("popup handler error: %s", e)


# ---- helpers ----
def _filter_ctx(fn: Callable, kwargs: dict) -> dict:
    """Keep only kwargs that `fn` accepts, so transition helpers can ignore
    extras without TypeError."""
    import inspect
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}
