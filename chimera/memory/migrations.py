"""Lightweight schema-version migrations. If the user has an existing v1 DB
(selectors table without app_version) we rescue its data; otherwise we just
run schemas.sql."""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = "3"


def run(con: sqlite3.Connection, schema_ddl: str):
    cur = con.cursor()

    # Ensure meta table exists first so we can read/write schema version.
    cur.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    current = cur.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    current = current[0] if current else None

    if current == SCHEMA_VERSION:
        # schema already at target; still run IF NOT EXISTS blocks harmlessly
        cur.executescript(schema_ddl)
        con.commit()
        return

    # v1 → v2: selectors table exists but lacks app_version column
    if current is None and _table_exists(cur, "selectors"):
        cols = {r[1] for r in cur.execute("PRAGMA table_info(selectors)").fetchall()}
        if "app_version" not in cols:
            _migrate_v1_to_v2(cur)

    cur.executescript(schema_ddl)
    cur.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SCHEMA_VERSION,),
    )
    con.commit()


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _migrate_v1_to_v2(cur: sqlite3.Cursor):
    cur.execute("ALTER TABLE selectors RENAME TO selectors_v1")
    # New table will be created by schemas.sql. After that, caller should
    # copy-over: done inline here because schemas.sql runs next.
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS selectors (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        app_package       TEXT NOT NULL,
        app_version       TEXT NOT NULL DEFAULT '',
        screen_fp         TEXT NOT NULL,
        role              TEXT NOT NULL,
        element_fp        TEXT NOT NULL,
        primary_expr      TEXT NOT NULL,
        primary_strategy  TEXT NOT NULL,
        primary_score     REAL NOT NULL,
        fallbacks_json    TEXT NOT NULL,
        description       TEXT NOT NULL DEFAULT '',
        provenance        TEXT NOT NULL DEFAULT 'learned',
        last_ok           REAL NOT NULL DEFAULT 0,
        failures          INTEGER NOT NULL DEFAULT 0,
        version           INTEGER NOT NULL DEFAULT 1,
        created_at        REAL NOT NULL DEFAULT 0,
        UNIQUE(app_package, app_version, screen_fp, role)
    );
    """)
    cur.execute("""
        INSERT INTO selectors (
            app_package, app_version, screen_fp, role, element_fp,
            primary_expr, primary_strategy, primary_score, fallbacks_json,
            description, last_ok, failures, version)
        SELECT app_package, '', screen_fp, role, element_fp,
               primary_expr, primary_strategy, primary_score, fallbacks_json,
               description, last_ok, failures, version
        FROM selectors_v1
    """)
    cur.execute("DROP TABLE selectors_v1")
