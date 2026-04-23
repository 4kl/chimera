-- Chimera memory schema (v2 — version-aware).
-- Lookup key: (app_package, app_version, screen_fp, role).
-- screen_fp may be '*' for "any screen of this app/version".

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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
    provenance        TEXT NOT NULL DEFAULT 'learned',  -- learned | healed | migrated
    last_ok           REAL NOT NULL DEFAULT 0,
    failures          INTEGER NOT NULL DEFAULT 0,
    version           INTEGER NOT NULL DEFAULT 1,       -- bundle revision
    created_at        REAL NOT NULL DEFAULT 0,
    UNIQUE(app_package, app_version, screen_fp, role)
);

CREATE INDEX IF NOT EXISTS ix_sel_lookup
    ON selectors(app_package, app_version, screen_fp, role);
CREATE INDEX IF NOT EXISTS ix_sel_role
    ON selectors(app_package, role);

-- Per-version profile: which screens has this version exposed to us, when
-- did we first/last see it. Used for version-similarity detection.
CREATE TABLE IF NOT EXISTS app_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_package     TEXT NOT NULL,
    app_version     TEXT NOT NULL,
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    screen_fps_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(app_package, app_version)
);

-- Ledger of version-to-version migrations for audit + rollback.
CREATE TABLE IF NOT EXISTS version_migrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    app_package   TEXT NOT NULL,
    from_version  TEXT NOT NULL,
    to_version    TEXT NOT NULL,
    jaccard       REAL NOT NULL,
    roles_copied  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    app_package    TEXT NOT NULL,
    app_version    TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL,
    outcome        TEXT NOT NULL,  -- learned | ok | fail_primary | fail_all | healed | migrated
    selector_expr  TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_events_lookup
    ON events(app_package, role, ts DESC);

-- ===========================================================================
-- State machine: semantic screen identities + transitions between them.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS states (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    app_package        TEXT NOT NULL,
    app_version        TEXT NOT NULL DEFAULT '',
    name               TEXT NOT NULL,
    features_json      TEXT NOT NULL DEFAULT '[]',
    fingerprints_json  TEXT NOT NULL DEFAULT '[]',
    allowed_roles_json TEXT NOT NULL DEFAULT '[]',
    confidence         REAL NOT NULL DEFAULT 0.5,
    first_seen         REAL NOT NULL DEFAULT 0,
    last_seen          REAL NOT NULL DEFAULT 0,
    UNIQUE(app_package, app_version, name)
);

CREATE INDEX IF NOT EXISTS ix_states_app
    ON states(app_package, app_version);

CREATE TABLE IF NOT EXISTS state_transitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    app_package   TEXT NOT NULL,
    app_version   TEXT NOT NULL DEFAULT '',
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    role          TEXT NOT NULL,
    action        TEXT NOT NULL,
    success       INTEGER NOT NULL DEFAULT 0,
    failure       INTEGER NOT NULL DEFAULT 0,
    last_ok       REAL NOT NULL DEFAULT 0,
    UNIQUE(app_package, app_version, from_state, role, action, to_state)
);

CREATE INDEX IF NOT EXISTS ix_trans_from
    ON state_transitions(app_package, app_version, from_state);
CREATE INDEX IF NOT EXISTS ix_trans_to
    ON state_transitions(app_package, app_version, to_state);
