"""Inspect the Chimera selector cache.

Usage:
    python scripts/inspect_db.py                    # list all apps with counts
    python scripts/inspect_db.py com.whatsapp       # list roles for an app
    python scripts/inspect_db.py com.whatsapp 2.24  # full bundle dump
    python scripts/inspect_db.py --events           # recent event log
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def list_apps(con: sqlite3.Connection) -> None:
    rows = con.execute("""
        SELECT app_package, app_version, COUNT(*) AS n_roles,
               MAX(last_ok) AS last_ok
        FROM selectors
        GROUP BY app_package, app_version
        ORDER BY app_package, app_version
    """).fetchall()
    if not rows:
        print("(empty — no selectors learned yet)")
        return
    print(f"{'app_package':<42}  {'version':<16}  roles  last_ok")
    print("-" * 85)
    for r in rows:
        ts = r["last_ok"]
        age = (time.time() - ts) / 60 if ts else -1
        age_str = f"{age:.1f}m ago" if ts else "never"
        print(f"{r['app_package']:<42}  {r['app_version'] or '-':<16}  "
              f"{r['n_roles']:>5}  {age_str}")


def list_roles(con: sqlite3.Connection, app: str,
               version: str | None = None) -> None:
    where = "app_package = ?"
    params: list = [app]
    if version:
        where += " AND app_version LIKE ?"
        params.append(f"{version}%")
    rows = con.execute(f"""
        SELECT app_version, role, screen_fp, primary_strategy,
               primary_score, provenance, failures, last_ok,
               substr(primary_expr, 1, 70) AS expr
        FROM selectors WHERE {where}
        ORDER BY app_version, role, screen_fp
    """, params).fetchall()
    if not rows:
        print(f"(no selectors for {app}{' v=' + version if version else ''})")
        return
    print(f"\n{app}"
          + (f" @ {version}*" if version else " (all versions)")
          + f"  —  {len(rows)} bundle(s)\n")
    print(f"{'version':<12} {'role':<22} {'strat':<16} "
          f"{'prov':<9} {'scr':<10} {'fail':>4}  expr")
    print("-" * 110)
    for r in rows:
        scr = r["screen_fp"][:8]
        print(f"{(r['app_version'] or '-'):<12} {r['role']:<22} "
              f"{r['primary_strategy']:<16} "
              f"{r['provenance']:<9} {scr:<10} "
              f"{r['failures']:>4}  {r['expr']}")


def full_dump(con: sqlite3.Connection, app: str, version: str) -> None:
    rows = con.execute("""
        SELECT * FROM selectors WHERE app_package=? AND app_version LIKE ?
        ORDER BY role, screen_fp
    """, (app, f"{version}%")).fetchall()
    if not rows:
        print(f"(no selectors for {app} @ {version}*)")
        return
    for r in rows:
        print(f"\n=== role={r['role']} (version={r['app_version']}, "
              f"screen={r['screen_fp'][:8]}) ===")
        print(f"  primary ({r['primary_strategy']}, {r['primary_score']:.2f}, "
              f"{r['provenance']}):")
        print(f"    {r['primary_expr']}")
        fbs = json.loads(r["fallbacks_json"])
        for fb in fbs:
            print(f"  fallback ({fb['strategy']}, {fb['score']:.2f}):")
            print(f"    {fb['expr']}")
        print(f"  description: {r['description']}")
        print(f"  failures: {r['failures']}, last_ok: {r['last_ok']}, "
              f"revision: {r['version']}")


def events(con: sqlite3.Connection, limit: int = 30) -> None:
    rows = con.execute("""
        SELECT ts, app_package, app_version, role, outcome,
               substr(selector_expr, 1, 60) AS expr, note
        FROM events ORDER BY ts DESC LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        print("(no events)")
        return
    print(f"{'when':<10} {'outcome':<14} {'app':<30} "
          f"{'ver':<10} {'role':<22}")
    print("-" * 95)
    now = time.time()
    for r in rows:
        age = (now - r["ts"]) / 60
        print(f"{age:>6.1f}m    {r['outcome']:<14} {r['app_package']:<30} "
              f"{(r['app_version'] or '-'):<10} {r['role']:<22}")


def states(con: sqlite3.Connection, app: str | None = None) -> None:
    where, params = "", []
    if app:
        where = "WHERE app_package = ?"
        params.append(app)
    rows = con.execute(
        f"SELECT app_package, app_version, name, confidence, "
        f"       fingerprints_json, allowed_roles_json "
        f"FROM states {where} ORDER BY app_package, app_version, name", params
    ).fetchall()
    if not rows:
        print("(no states learned yet)")
        return
    print(f"{'app_package':<42} {'ver':<10} {'state':<20} "
          f"{'conf':>5}  fps  roles")
    print("-" * 100)
    for r in rows:
        fps = len(json.loads(r["fingerprints_json"]))
        roles = json.loads(r["allowed_roles_json"])
        role_str = ",".join(roles[:3]) + ("…" if len(roles) > 3 else "")
        print(f"{r['app_package']:<42} {(r['app_version'] or '-'):<10} "
              f"{r['name']:<20} {r['confidence']:>5.2f}  {fps:>3}  {role_str}")


def graph(con: sqlite3.Connection, app: str,
          version: str | None = None) -> None:
    where = "app_package = ?"
    params: list = [app]
    if version:
        where += " AND app_version LIKE ?"
        params.append(f"{version}%")
    rows = con.execute(
        f"SELECT app_version, from_state, to_state, role, action, "
        f"       success, failure, last_ok "
        f"FROM state_transitions WHERE {where} "
        f"ORDER BY app_version, from_state, to_state", params
    ).fetchall()
    if not rows:
        print(f"(no transitions for {app})")
        return
    print(f"\n{app}"
          + (f" @ {version}*" if version else "")
          + f"  —  {len(rows)} edge(s)\n")
    print(f"{'ver':<10} {'from':<18} {'role':<18} {'action':<6} "
          f"→ {'to':<18} {'conf':>5}  (ok/fail)")
    print("-" * 95)
    for r in rows:
        total = (r["success"] or 0) + (r["failure"] or 0)
        conf = (r["success"] + 1) / (total + 2) if total else 0.5
        print(f"{(r['app_version'] or '-'):<10} "
              f"{r['from_state']:<18} {r['role']:<18} "
              f"{r['action']:<6} → {r['to_state']:<18} "
              f"{conf:>5.2f}  ({r['success']}/{r['failure']})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Inspect the Chimera selector DB.")
    p.add_argument("app", nargs="?", help="package id")
    p.add_argument("version", nargs="?", help="version prefix")
    p.add_argument("--db", default="chimera.db")
    p.add_argument("--events", action="store_true",
                   help="show recent event log")
    p.add_argument("--full", action="store_true",
                   help="full bundle dump (requires app and version)")
    p.add_argument("--states", action="store_true",
                   help="list learned UI states")
    p.add_argument("--graph", action="store_true",
                   help="list state transitions (requires app)")
    args = p.parse_args(argv)

    con = connect(args.db)
    if args.events:
        events(con)
    elif args.states:
        states(con, args.app)
    elif args.graph:
        if not args.app:
            print("--graph requires an app argument")
            return 2
        graph(con, args.app, args.version)
    elif args.app and args.version and args.full:
        full_dump(con, args.app, args.version)
    elif args.app:
        list_roles(con, args.app, args.version)
    else:
        list_apps(con)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
