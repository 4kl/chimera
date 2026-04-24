from __future__ import annotations

import logging
import re
import time
from typing import Optional

from .core.driver import Driver
from .core.errors import ChimeraError, ExecutionError
from .core.models import ActionStep, Intent, RunCtx, Session
from .execution.executor import Executor
from .healing.healer import Healer
from .learning.engine import LearningEngine
from .memory.store import Memory
from .profiler import AppProfiler, AppResolver, normalize_app_name
from .reasoning.matcher import llm_plan
from .reasoning.ollama_client import Ollama
from .selector.generator import SelectorGenerator
from .state.detector import StateDetector
from .state.navigator import Navigator
from .state.store import StateStore

log = logging.getLogger("chimera")


# Hand-curated fallbacks for commonly-renamed packages. AppResolver's dynamic
# device scan takes priority over this table.
APP_ALIAS = {
    "whatsapp":   "com.whatsapp",
    "telegram":   "org.telegram.messenger",
    "chrome":     "com.android.chrome",
    "gmail":      "com.google.android.gm",
    "settings":   "com.android.settings",
    "youtube":    "com.google.android.youtube",
    "maps":       "com.google.android.apps.maps",
    "photos":     "com.google.android.apps.photos",
    "play store": "com.android.vending",
    "play":       "com.android.vending",
    "camera":     "com.android.camera2",      # overridden by resolver if OEM differs
    "calculator": "com.google.android.calculator",
    "clock":      "com.google.android.deskclock",
    "calendar":   "com.google.android.calendar",
    "contacts":   "com.google.android.contacts",
    "files":      "com.google.android.documentsui",
    "messages":   "com.google.android.apps.messaging",
    "phone":      "com.google.android.dialer",
}


# Matches "open/launch/start/run/go to/use [the] <NAME> [app]" at the start
# of a command. <NAME> captures up to 3 words so "play store" / "google maps"
# still parse. Cuts off at a connector (and/then/to/,/;) or end-of-string.
_OPEN_APP_RE = re.compile(
    r"^\s*(?:open|launch|start|run|use|go\s+to|navigate\s+to)"
    r"\s+(?:the\s+)?"
    r"(?P<name>[A-Za-z][A-Za-z0-9 ]{0,30}?)"
    r"(?:\s+app)?"
    r"(?:"
    r"\s+(?:and|then|to|with|in)\b"   # connector word — whitespace required
    r"|\s*[,;]"                        # punctuation connector
    r"|\s*$"                           # end of command
    r")",
    re.I,
)


def _extract_app_hint(nl: str) -> Optional[str]:
    m = _OPEN_APP_RE.match(nl or "")
    if not m:
        return None
    name = m.group("name").strip().lower()
    # Strip trailing fillers like "please"
    name = re.sub(r"\s+(please|now)$", "", name).strip()
    return name or None


class Chimera:
    def __init__(self,
                 db_path: str = "chimera.db",
                 serial: Optional[str] = None,
                 appium_url: Optional[str] = None,
                 ollama_url: Optional[str] = None,
                 ollama_model: Optional[str] = None,
                 max_heal_retries: int = 1):
        self.d = Driver.connect(serial=serial, appium_url=appium_url)
        self.llm = Ollama(url=ollama_url, model=ollama_model)
        self.mem = Memory(db_path)
        self.profiler = AppProfiler(self.d)
        self.resolver = AppResolver(self.d, static_alias=APP_ALIAS)
        self.gen = SelectorGenerator()
        self.heal = Healer()
        self.learning = LearningEngine(self.mem)
        # State subsystem shares the Memory SQLite connection.
        self.state_store = StateStore(self.mem.connection)
        self.state_detector = StateDetector(self.state_store, self.llm)
        self.navigator = Navigator(self.state_detector, self.state_store)
        self.exec = Executor(
            self.d, self.mem, self.gen, self.llm, self.heal, self.learning,
            state_detector=self.state_detector,
            state_store=self.state_store,
            navigator=self.navigator,
        )
        self.max_heal_retries = max_heal_retries

    # ---------- public ----------
    def run(self, nl_command: str) -> dict:
        # Seed the planner with known state names for whatever app the
        # command appears to target (regex-extracted), so it can reuse them
        # instead of inventing new ones each run.
        hint = _extract_app_hint(nl_command)
        known_states: list[str] = []
        if hint:
            pkg_guess = self._resolve_app(hint) or ""
            if pkg_guess:
                known_states = sorted({
                    s.name for s in self.state_store.all_versions_states(pkg_guess)
                })
        intent = llm_plan(self.llm, nl_command, known_states=known_states or None)
        intent = self._normalize_plan(nl_command, intent)
        log.info("plan for %r: %d steps (app_hint=%s)",
                 nl_command, len(intent.steps), intent.app_hint)

        app = self._resolve_app(intent.app_hint)
        version = ""
        if app:
            profile = self.profiler.of(app)
            version = profile.version
            log.info("target: %s (%s) v=%s",
                     normalize_app_name(app), app, version or "unknown")
        elif intent.app_hint:
            log.warning("could not resolve app_hint=%r to an installed "
                        "package; plan will run on the current screen",
                        intent.app_hint)

        ctx = RunCtx(app=app, app_version=version, session=Session())
        self._ensure_app(ctx, intent)

        # Lazy version migration on first sight.
        if app and version:
            copied = self.learning.warm_up_version(app, version)
            if copied:
                log.info("warmed %d selectors into %s@%s from a similar version",
                         copied, app, version)

        for i, step in enumerate(intent.steps):
            log.info("step %d/%d role=%s action=%s",
                     i + 1, len(intent.steps), step.role, step.action)
            self._run_with_retry(step, ctx)
            time.sleep(0.5)

        summary = ctx.session.summary() if ctx.session else {}
        log.info("session: %s", summary)
        return summary

    # ---------- internals ----------
    def _normalize_plan(self, nl_command: str, intent: Intent) -> Intent:
        """Enforce: if the user asked to open an app, the plan starts with a
        launch step. Handles the common case where qwen returns app_hint=None
        and a first step that tries to locate the app as a UI element."""
        inferred = _extract_app_hint(nl_command)
        if not inferred:
            return intent

        if not intent.app_hint:
            intent.app_hint = inferred
        first = intent.steps[0] if intent.steps else None
        if first and first.action == "launch":
            if not (first.value or "").strip():
                first.value = intent.app_hint
            return intent

        # Drop any leading step that was trying to "tap the app icon" on the
        # current screen — we'll launch directly via the package manager.
        if first and first.role in {"app", "app_icon", f"{inferred}_icon",
                                    f"{inferred}_app"}:
            intent.steps = intent.steps[1:]

        intent.steps.insert(0, ActionStep(
            role="app",
            action="launch",
            value=intent.app_hint,
            description=f"launch the {inferred} app",
        ))
        log.info("injected launch step for inferred app=%r", inferred)
        return intent

    def _resolve_app(self, hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        h = hint.strip().lower()
        # 1. exact static alias
        if h in APP_ALIAS:
            pkg = APP_ALIAS[h]
            # Confirm it's actually installed; if not, fall through to resolver
            if self.resolver.resolve(pkg) == pkg:
                return pkg
        # 2. package-id pattern, verified installed
        if "." in h:
            if self.resolver.resolve(h) == h:
                return h
            return h  # trust the user even if not in our pm list output
        # 3. dynamic resolver (pm list packages on device)
        pkg = self.resolver.resolve(h)
        if pkg:
            return pkg
        return APP_ALIAS.get(h)  # last-ditch static

    def _ensure_app(self, ctx: RunCtx, intent):
        if not ctx.app:
            return
        try:
            cur = self.d.current_app().get("package", "")
        except Exception:
            cur = ""
        if cur == ctx.app:
            return
        first = intent.steps[0] if intent.steps else None
        if first and first.action == "launch":
            return
        log.info("launching %s (current: %s)", ctx.app, cur or "?")
        if cur and cur != ctx.app:
            self.d.press("home")
            time.sleep(0.4)
        self.d.launch(ctx.app)
        time.sleep(2.0)
        self.profiler.invalidate(ctx.app)
        ctx.app_version = self.profiler.of(ctx.app).version

    def _run_with_retry(self, step: ActionStep, ctx: RunCtx):
        attempts = 0
        last_err: Optional[Exception] = None
        while attempts <= self.max_heal_retries:
            try:
                self.exec.run_step(step, ctx)
                return
            except ExecutionError as e:
                last_err = e
                attempts += 1
                log.warning("step failed (%s); heal retry %d/%d",
                            e, attempts, self.max_heal_retries)
                frame = self.exec.perceive()
                app = ctx.app or frame.package
                bundle = self.mem.get(app, ctx.app_version, frame.fp, step.role)
                if bundle is not None:
                    self.heal.heal(bundle, frame, self.llm, self.gen, self.mem,
                                   description=step.description)
        raise ChimeraError(f"step {step.role!r} exhausted retries: {last_err}")

    def close(self):
        self.mem.close()
