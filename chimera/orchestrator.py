from __future__ import annotations

import logging
import time
from typing import Optional

from .core.driver import Driver
from .core.errors import ChimeraError, ExecutionError
from .core.models import ActionStep, RunCtx, Session
from .execution.executor import Executor
from .healing.healer import Healer
from .learning.engine import LearningEngine
from .memory.store import Memory
from .profiler.app_profiler import AppProfiler, normalize_app_name
from .reasoning.matcher import llm_plan
from .reasoning.ollama_client import Ollama
from .selector.generator import SelectorGenerator

log = logging.getLogger("chimera")


# Alias table for name → package resolution. Not selectors — just the map
# from a user-facing word ("whatsapp") to an Android package id.
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
}


class Chimera:
    def __init__(self,
                 db_path: str = "chimera.db",
                 serial: Optional[str] = None,
                 ollama_url: Optional[str] = None,
                 ollama_model: Optional[str] = None,
                 max_heal_retries: int = 1):
        self.d = Driver.connect(serial=serial)
        self.llm = Ollama(url=ollama_url, model=ollama_model)
        self.mem = Memory(db_path)
        self.profiler = AppProfiler(self.d)
        self.gen = SelectorGenerator()
        self.heal = Healer()
        self.learning = LearningEngine(self.mem)
        self.exec = Executor(self.d, self.mem, self.gen, self.llm,
                             self.heal, self.learning)
        self.max_heal_retries = max_heal_retries

    # ---------- public ----------
    def run(self, nl_command: str) -> dict:
        intent = llm_plan(self.llm, nl_command)
        log.info("plan for %r: %d steps (app_hint=%s)",
                 nl_command, len(intent.steps), intent.app_hint)

        app = self._resolve_app(intent.app_hint)
        version = ""
        if app:
            profile = self.profiler.of(app)
            version = profile.version
            log.info("target: %s (%s) v=%s",
                     normalize_app_name(app), app, version or "unknown")

        ctx = RunCtx(app=app, app_version=version, session=Session())
        self._ensure_app(ctx, intent)

        # If we've never seen this (app, version) before, try a lazy migration
        # from a prior version based on screen-fingerprint similarity. This
        # populates the cache with 'migrated' bundles; each one revalidates on
        # first use and is promoted or healed accordingly.
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
    def _resolve_app(self, hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        h = hint.strip().lower()
        if h in APP_ALIAS:
            return APP_ALIAS[h]
        if "." in h:
            return h
        return APP_ALIAS.get(h)

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
        log.info("launching %s", ctx.app)
        self.d.launch(ctx.app)
        time.sleep(2.0)
        # Re-resolve version once the app is in the foreground (first install
        # might not have been known to uiautomator2 before launch).
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
