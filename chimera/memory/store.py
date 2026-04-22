from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from ..core.models import SelectorBundle, SelectorCandidate
from . import migrations

_SCHEMA = Path(__file__).with_name("schemas.sql")

WILDCARD_SCREEN = "*"


# ---------- confidence decay ----------
def decayed_score(base: float, last_ok: float, failures: int,
                  now: Optional[float] = None) -> float:
    """Effective confidence after aging + failure penalty.

    - Never below 0.1.
    - Age adds up to 0.4 decay (~40 days to saturate).
    - Each failure adds 0.15, up to 0.45.
    """
    t = now if now is not None else time.time()
    age_days = max(0.0, (t - last_ok) / 86400.0) if last_ok > 0 else 30.0
    penalty = min(0.4, age_days * 0.01) + min(0.45, failures * 0.15)
    return max(0.1, base * (1.0 - penalty))


# ---------- store ----------
class Memory:
    def __init__(self, path: str = "chimera.db"):
        self.path = path
        self._con = sqlite3.connect(self.path)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        ddl = _SCHEMA.read_text()
        migrations.run(self._con, ddl)

    # ================= selectors =================
    def get(self, app: str, version: str, screen_fp: str, role: str
            ) -> Optional[SelectorBundle]:
        """Tiered lookup:
          1) exact (app, version, screen_fp, role)
          2) (app, version, '*', role)
          3) most-recent (app, any other version, screen_fp, role)  — migration seed
          4) most-recent (app, any other version, '*', role)        — migration seed
        Seeds copied from another version are marked provenance='migrated' and
        their primary_score is scaled down; they must re-validate before trust.
        """
        row = self._exact(app, version, screen_fp, role)
        if row:
            return self._row_to_bundle(row)

        row = self._exact(app, version, WILDCARD_SCREEN, role)
        if row:
            return self._row_to_bundle(row)

        # version-cross seeds
        seed = self._latest_other_version(app, version, screen_fp, role) \
            or self._latest_other_version(app, version, WILDCARD_SCREEN, role)
        if seed is not None:
            bundle = self._row_to_bundle(seed, provenance_override="migrated",
                                         score_scale=0.8)
            bundle.app_version = version
            bundle.screen_fingerprint = screen_fp
            return bundle
        return None

    def put(self, b: SelectorBundle):
        with self._con:
            self._con.execute(
                """
                INSERT INTO selectors (
                    app_package, app_version, screen_fp, role, element_fp,
                    primary_expr, primary_strategy, primary_score,
                    fallbacks_json, description, provenance,
                    last_ok, failures, version, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(app_package, app_version, screen_fp, role) DO UPDATE SET
                    element_fp       = excluded.element_fp,
                    primary_expr     = excluded.primary_expr,
                    primary_strategy = excluded.primary_strategy,
                    primary_score    = excluded.primary_score,
                    fallbacks_json   = excluded.fallbacks_json,
                    description      = excluded.description,
                    provenance       = excluded.provenance,
                    last_ok          = excluded.last_ok,
                    failures         = excluded.failures,
                    version          = excluded.version
                """,
                (
                    b.app_package, b.app_version, b.screen_fingerprint or WILDCARD_SCREEN,
                    b.semantic_role, b.element_fingerprint,
                    b.primary.expr, b.primary.strategy, b.primary.score,
                    json.dumps([c.to_dict() for c in b.fallbacks]),
                    b.description, b.primary.provenance,
                    b.last_ok, b.failures, b.version, time.time(),
                ),
            )

    def bump_failure(self, b: SelectorBundle):
        b.failures += 1
        with self._con:
            self._con.execute(
                "UPDATE selectors SET failures=? "
                "WHERE app_package=? AND app_version=? AND screen_fp=? AND role=?",
                (b.failures, b.app_package, b.app_version,
                 b.screen_fingerprint or WILDCARD_SCREEN, b.semantic_role),
            )

    def description_for(self, b: SelectorBundle) -> str:
        return b.description or b.semantic_role.replace("_", " ")

    # ================= app profiles (screen-fp ledger) =================
    def record_screen(self, app: str, version: str, screen_fp: str):
        now = time.time()
        with self._con:
            row = self._con.execute(
                "SELECT screen_fps_json FROM app_profiles "
                "WHERE app_package=? AND app_version=?",
                (app, version),
            ).fetchone()
            if row:
                fps = set(json.loads(row["screen_fps_json"]))
                fps.add(screen_fp)
                self._con.execute(
                    "UPDATE app_profiles SET last_seen=?, screen_fps_json=? "
                    "WHERE app_package=? AND app_version=?",
                    (now, json.dumps(sorted(fps)), app, version),
                )
            else:
                self._con.execute(
                    "INSERT INTO app_profiles "
                    "(app_package, app_version, first_seen, last_seen, screen_fps_json) "
                    "VALUES (?,?,?,?,?)",
                    (app, version, now, now, json.dumps([screen_fp])),
                )

    def known_screens(self, app: str, version: str) -> set[str]:
        row = self._con.execute(
            "SELECT screen_fps_json FROM app_profiles "
            "WHERE app_package=? AND app_version=?",
            (app, version),
        ).fetchone()
        return set(json.loads(row["screen_fps_json"])) if row else set()

    def versions_for(self, app: str) -> list[str]:
        rows = self._con.execute(
            "SELECT app_version FROM app_profiles WHERE app_package=? "
            "ORDER BY last_seen DESC",
            (app,),
        ).fetchall()
        return [r["app_version"] for r in rows]

    def version_similarity(self, app: str, a: str, b: str) -> float:
        """Jaccard overlap of known screen fingerprints between two versions."""
        sa, sb = self.known_screens(app, a), self.known_screens(app, b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def find_similar_version(self, app: str, new_version: str,
                             min_jaccard: float = 0.5) -> Optional[str]:
        best, best_score = None, 0.0
        for v in self.versions_for(app):
            if v == new_version or not v:
                continue
            score = self.version_similarity(app, new_version, v)
            if score > best_score:
                best, best_score = v, score
        if best and best_score >= min_jaccard:
            return best
        return None

    def migrate_from(self, app: str, from_version: str, to_version: str,
                     jaccard: float) -> int:
        """Bulk-copy selectors from a prior version; scores scaled; provenance
        tagged 'migrated'. Rows whose (screen_fp, role) already exist for the
        target version are left untouched."""
        rows = self._con.execute(
            "SELECT * FROM selectors WHERE app_package=? AND app_version=?",
            (app, from_version),
        ).fetchall()
        copied = 0
        now = time.time()
        with self._con:
            for r in rows:
                exists = self._con.execute(
                    "SELECT 1 FROM selectors WHERE app_package=? AND app_version=? "
                    "AND screen_fp=? AND role=?",
                    (app, to_version, r["screen_fp"], r["role"]),
                ).fetchone()
                if exists:
                    continue
                self._con.execute(
                    """
                    INSERT INTO selectors (
                        app_package, app_version, screen_fp, role, element_fp,
                        primary_expr, primary_strategy, primary_score,
                        fallbacks_json, description, provenance,
                        last_ok, failures, version, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        app, to_version, r["screen_fp"], r["role"], r["element_fp"],
                        r["primary_expr"], r["primary_strategy"],
                        r["primary_score"] * 0.8,
                        r["fallbacks_json"], r["description"], "migrated",
                        0.0, 0, r["version"], now,
                    ),
                )
                copied += 1
            self._con.execute(
                "INSERT INTO version_migrations "
                "(ts, app_package, from_version, to_version, jaccard, roles_copied) "
                "VALUES (?,?,?,?,?,?)",
                (now, app, from_version, to_version, jaccard, copied),
            )
        return copied

    # ================= events =================
    def log(self, app: str, version: str, role: str, outcome: str,
            expr: str = "", note: str = ""):
        with self._con:
            self._con.execute(
                "INSERT INTO events "
                "(ts, app_package, app_version, role, outcome, selector_expr, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (time.time(), app, version, role, outcome, expr, note),
            )

    def recent_failures(self, app: str, role: str, limit: int = 10) -> list[dict]:
        rows = self._con.execute(
            "SELECT ts, app_version, outcome, selector_expr, note FROM events "
            "WHERE app_package=? AND role=? AND outcome LIKE 'fail%' "
            "ORDER BY ts DESC LIMIT ?",
            (app, role, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ================= internals =================
    def _exact(self, app: str, version: str, screen_fp: str, role: str
               ) -> Optional[sqlite3.Row]:
        return self._con.execute(
            "SELECT * FROM selectors WHERE app_package=? AND app_version=? "
            "AND screen_fp=? AND role=?",
            (app, version, screen_fp, role),
        ).fetchone()

    def _latest_other_version(self, app: str, current_version: str,
                              screen_fp: str, role: str
                              ) -> Optional[sqlite3.Row]:
        return self._con.execute(
            "SELECT s.* FROM selectors s "
            "LEFT JOIN app_profiles p ON p.app_package=s.app_package "
            "  AND p.app_version=s.app_version "
            "WHERE s.app_package=? AND s.app_version<>? "
            "  AND s.screen_fp=? AND s.role=? "
            "ORDER BY COALESCE(p.last_seen, s.last_ok) DESC LIMIT 1",
            (app, current_version, screen_fp, role),
        ).fetchone()

    def _row_to_bundle(self, row: sqlite3.Row,
                       provenance_override: Optional[str] = None,
                       score_scale: float = 1.0) -> SelectorBundle:
        fallbacks = [SelectorCandidate.from_dict(d)
                     for d in json.loads(row["fallbacks_json"])]
        for c in fallbacks:
            c.score *= score_scale
            if provenance_override:
                c.provenance = provenance_override
        primary = SelectorCandidate(
            expr=row["primary_expr"],
            strategy=row["primary_strategy"],
            score=row["primary_score"] * score_scale,
            provenance=provenance_override or row["provenance"],
        )
        return SelectorBundle(
            primary=primary,
            fallbacks=fallbacks,
            semantic_role=row["role"],
            app_package=row["app_package"],
            app_version=row["app_version"],
            screen_fingerprint=row["screen_fp"],
            element_fingerprint=row["element_fp"],
            description=row["description"],
            last_ok=row["last_ok"],
            failures=row["failures"],
            version=row["version"],
        )

    def close(self):
        self._con.close()
