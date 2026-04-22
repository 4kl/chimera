"""Resolve (package, version) for the app currently in the foreground, or for
an arbitrary package. Cache per-session to avoid repeated `dumpsys` shelling."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AppProfile:
    package: str
    version: str           # versionName, e.g. "2.24.10.78"
    version_code: str = ""  # integer as string, may be ""

    @property
    def key(self) -> str:
        return f"{self.package}@{self.version or 'unknown'}"


_VERSION_NAME_RE = re.compile(r"versionName=([^\s]+)")
_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")


class AppProfiler:
    def __init__(self, driver):
        self.d = driver
        self._cache: dict[str, AppProfile] = {}

    # ---------- public ----------
    def current(self) -> AppProfile:
        pkg = self._current_package()
        return self.of(pkg)

    def of(self, package: str) -> AppProfile:
        if not package:
            return AppProfile(package="", version="")
        if package in self._cache:
            return self._cache[package]

        ver = code = ""
        # 1) Preferred: uiautomator2 app_info()
        try:
            info = self.d.app_info(package)
            ver = str(info.get("versionName", "") or "")
            code = str(info.get("versionCode", "") or "")
        except Exception:
            pass

        # 2) Fallback: dumpsys
        if not ver:
            ver, code = self._via_dumpsys(package)

        profile = AppProfile(package=package, version=ver, version_code=code)
        self._cache[package] = profile
        return profile

    def invalidate(self, package: Optional[str] = None):
        if package is None:
            self._cache.clear()
        else:
            self._cache.pop(package, None)

    # ---------- internals ----------
    def _current_package(self) -> str:
        try:
            return self.d.current_app().get("package", "") or ""
        except Exception:
            return ""

    def _via_dumpsys(self, package: str) -> tuple[str, str]:
        try:
            out = self.d.shell(f"dumpsys package {package} | grep -E 'versionName|versionCode'")
        except Exception:
            return "", ""
        if not out:
            return "", ""
        vn = _VERSION_NAME_RE.search(out)
        vc = _VERSION_CODE_RE.search(out)
        return (vn.group(1) if vn else "", vc.group(1) if vc else "")


def normalize_app_name(package: str) -> str:
    """Best-effort friendly name from a package id — for logging only.

    Heuristic: strip a TLD-style prefix (com/org/io/net); return the next
    segment. Imperfect for multi-segment Google packages, but fine for logs.
    """
    if not package:
        return ""
    parts = package.split(".")
    if parts[0] in {"com", "org", "io", "net"} and len(parts) > 1:
        return parts[1]
    return parts[0]
