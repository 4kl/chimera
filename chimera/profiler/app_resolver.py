"""Resolves common app names to installed package ids by querying the device.
Complements the small static APP_ALIAS table with whatever the user actually
has installed."""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("chimera.resolver")

# Tokens that are almost never the app's "name" — skip when indexing.
_GENERIC_TOKENS = {
    "com", "org", "io", "net", "co",
    "android", "google", "samsung", "oneplus", "xiaomi", "huawei", "motorola",
    "apps", "app", "mobile", "client",
}


class AppResolver:
    """Queries `pm list packages` once (per session) and answers
    common-name → package lookups like resolve("camera") →
    "com.android.camera2"."""

    def __init__(self, driver, static_alias: Optional[dict[str, str]] = None):
        self.d = driver
        self._static = {k.lower(): v for k, v in (static_alias or {}).items()}
        self._installed: Optional[list[str]] = None

    # ---------- public ----------
    def resolve(self, name: str) -> Optional[str]:
        if not name:
            return None
        key = name.strip().lower()
        if not key:
            return None

        # Already looks like a package id? Use it verbatim.
        if "." in key and self._is_installed(key):
            return key

        # Static alias first (cheap, curated).
        if key in self._static:
            return self._static[key]

        # Device scan, ranked by match quality.
        pkgs = self._packages()
        if not pkgs:
            return None
        best_pkg, best_score = None, 0
        for pkg in pkgs:
            score = self._score(key, pkg)
            if score > best_score:
                best_pkg, best_score = pkg, score
        if best_pkg and best_score > 0:
            log.debug("resolve(%s) → %s (score=%d)", key, best_pkg, best_score)
            return best_pkg
        return None

    def refresh(self):
        self._installed = None

    # ---------- internals ----------
    def _packages(self) -> list[str]:
        if self._installed is not None:
            return self._installed
        try:
            out = self.d.shell("pm list packages", timeout=10.0)
        except Exception as e:
            log.warning("pm list packages failed: %s", e)
            self._installed = []
            return []
        pkgs = []
        for line in (out or "").splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkgs.append(line.split("package:", 1)[1].strip())
        self._installed = pkgs
        return pkgs

    def _is_installed(self, pkg: str) -> bool:
        return pkg in self._packages()

    @staticmethod
    def _score(needle: str, pkg: str) -> int:
        """Higher = better. 0 = no match."""
        segs = [s.lower() for s in pkg.split(".")
                if s.lower() not in _GENERIC_TOKENS]
        if not segs:
            return 0
        # 5: whole needle equals a segment ("camera" == "camera2"? no → 3)
        # 4: a segment starts with needle ("camera2".startswith("camera"))
        # 3: needle is a substring of a segment
        # 2: segment is a substring of needle ("cam" in "camera")
        # 1: needle appears anywhere in the package id
        for s in segs:
            if s == needle:
                return 5
        for s in segs:
            if s.startswith(needle):
                return 4
        for s in segs:
            if needle in s:
                return 3
        for s in segs:
            if s in needle and len(s) >= 3:
                return 2
        if needle in pkg.lower():
            return 1
        return 0
