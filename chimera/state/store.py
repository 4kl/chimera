"""CRUD over the states and state_transitions tables. Reuses the same SQLite
connection as the Memory layer by accepting an open connection — keeps all
learning data in one file."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from .models import StateFeature, StateTransition, UIState


class StateStore:
    def __init__(self, con: sqlite3.Connection):
        self._con = con

    # ============ states ============
    def upsert_state(self, s: UIState):
        with self._con:
            self._con.execute(
                """
                INSERT INTO states (
                    app_package, app_version, name, features_json,
                    fingerprints_json, allowed_roles_json, confidence,
                    first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(app_package, app_version, name) DO UPDATE SET
                    features_json      = excluded.features_json,
                    fingerprints_json  = excluded.fingerprints_json,
                    allowed_roles_json = excluded.allowed_roles_json,
                    confidence         = excluded.confidence,
                    last_seen          = excluded.last_seen
                """,
                (s.app_package, s.app_version, s.name,
                 json.dumps([f.to_dict() for f in s.features]),
                 json.dumps(s.fingerprints),
                 json.dumps(s.allowed_roles),
                 s.confidence,
                 s.first_seen or time.time(),
                 s.last_seen or time.time()),
            )

    def get_state(self, app: str, version: str, name: str) -> Optional[UIState]:
        row = self._con.execute(
            "SELECT * FROM states WHERE app_package=? AND app_version=? AND name=?",
            (app, version, name),
        ).fetchone()
        return self._row_to_state(row) if row else None

    def list_states(self, app: str, version: str) -> list[UIState]:
        rows = self._con.execute(
            "SELECT * FROM states WHERE app_package=? AND app_version=?",
            (app, version),
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def all_versions_states(self, app: str) -> list[UIState]:
        """Used for cross-version priors: return states from any version of
        the same app, so the detector can match against them when the current
        version has no states yet."""
        rows = self._con.execute(
            "SELECT * FROM states WHERE app_package=?",
            (app,),
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def find_state_by_fingerprint(self, app: str, version: str, fp: str
                                  ) -> Optional[UIState]:
        rows = self._con.execute(
            "SELECT * FROM states WHERE app_package=? AND app_version=?",
            (app, version),
        ).fetchall()
        for r in rows:
            fps = json.loads(r["fingerprints_json"])
            if fp in fps:
                return self._row_to_state(r)
        return None

    def delete_state(self, app: str, version: str, name: str):
        with self._con:
            self._con.execute(
                "DELETE FROM states WHERE app_package=? AND app_version=? AND name=?",
                (app, version, name),
            )
            self._con.execute(
                "DELETE FROM state_transitions WHERE app_package=? AND app_version=? "
                "AND (from_state=? OR to_state=?)",
                (app, version, name, name),
            )

    # ============ transitions ============
    def record_transition(self, t: StateTransition, *, success: bool):
        row = self._con.execute(
            "SELECT id, last_ok FROM state_transitions "
            "WHERE app_package=? AND app_version=? AND from_state=? "
            "AND role=? AND action=? AND to_state=?",
            (t.app_package, t.app_version, t.from_state,
             t.role, t.action, t.to_state),
        ).fetchone()
        now = time.time()
        with self._con:
            if row:
                self._con.execute(
                    "UPDATE state_transitions "
                    "SET success=success+?, failure=failure+?, last_ok=? "
                    "WHERE id=?",
                    (1 if success else 0, 0 if success else 1,
                     now if success else row["last_ok"], row["id"]),
                )
            else:
                self._con.execute(
                    "INSERT INTO state_transitions "
                    "(app_package, app_version, from_state, to_state, role, "
                    " action, success, failure, last_ok) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (t.app_package, t.app_version, t.from_state, t.to_state,
                     t.role, t.action,
                     1 if success else 0, 0 if success else 1,
                     now if success else 0),
                )

    def transitions_for(self, app: str, version: str) -> list[StateTransition]:
        rows = self._con.execute(
            "SELECT * FROM state_transitions WHERE app_package=? AND app_version=?",
            (app, version),
        ).fetchall()
        return [self._row_to_trans(r) for r in rows]

    def transitions_from(self, app: str, version: str, state: str
                         ) -> list[StateTransition]:
        rows = self._con.execute(
            "SELECT * FROM state_transitions "
            "WHERE app_package=? AND app_version=? AND from_state=?",
            (app, version, state),
        ).fetchall()
        return [self._row_to_trans(r) for r in rows]

    # ============ internals ============
    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> UIState:
        return UIState(
            name=row["name"],
            app_package=row["app_package"],
            app_version=row["app_version"],
            features=[StateFeature.from_dict(d)
                      for d in json.loads(row["features_json"])],
            fingerprints=json.loads(row["fingerprints_json"]),
            allowed_roles=json.loads(row["allowed_roles_json"]),
            confidence=row["confidence"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )

    @staticmethod
    def _row_to_trans(row: sqlite3.Row) -> StateTransition:
        return StateTransition(
            from_state=row["from_state"],
            to_state=row["to_state"],
            role=row["role"],
            action=row["action"],
            app_package=row["app_package"],
            app_version=row["app_version"],
            success=row["success"],
            failure=row["failure"],
            last_ok=row["last_ok"],
        )
