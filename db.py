"""Keel datastore — SQLite schema + helpers (handoff §7).

One file, ``keel.db``. Stdlib ``sqlite3`` only; no ORM (keeps the POC legible
and dependency-light). Every table the flywheel writes to is declared here.

Deviation from handoff §7 (noted per §8): adds a ``scenarios`` table. §7 lists
customers/subscriptions/conversations/evals/guardrail_events/audit_log/themes/
signals but gives synth no place to store the *churn scenario* that drives a
conversation, nor the adversarial (jailbreak/off-scope) cases that feed the
guardrail red-team. ``scenarios`` is that home — one clean table for both.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    segment           TEXT NOT NULL,           -- SMB / Mid-Market / Enterprise / Consumer
    tenure_months     INTEGER NOT NULL,
    arpu              REAL NOT NULL,            -- monthly revenue, USD
    demographic_attr  TEXT NOT NULL,           -- synthetic fairness slice (group_a / group_b)
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                    INTEGER PRIMARY KEY,
    customer_id           INTEGER NOT NULL REFERENCES customers(id),
    plan                  TEXT NOT NULL,        -- Basic / Pro / Enterprise
    price                 REAL NOT NULL,        -- monthly price, USD
    status                TEXT NOT NULL,        -- active / paused / cancelled
    last_save_offer_days  INTEGER,              -- days since last save offer; NULL = never
    created_at            TEXT NOT NULL
);

-- Drives each conversation, and doubles as the adversarial test set (kind='adversarial').
CREATE TABLE IF NOT EXISTS scenarios (
    id               INTEGER PRIMARY KEY,
    customer_id      INTEGER REFERENCES customers(id),   -- NULL for pure adversarial probes
    kind             TEXT NOT NULL,            -- churn / adversarial
    churn_reason     TEXT,                     -- e.g. 'Price too high' (NULL for adversarial)
    persona          TEXT,                     -- tone/attitude for the simulated customer
    opening_message  TEXT NOT NULL,            -- first user turn
    is_adversarial   INTEGER NOT NULL DEFAULT 0,
    attack_type      TEXT,                     -- jailbreak / off_scope / pii_leak (adversarial only)
    eligible         INTEGER NOT NULL DEFAULT 1,  -- ground truth: savable under policy?
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id                INTEGER PRIMARY KEY,
    customer_id       INTEGER REFERENCES customers(id),
    scenario_id       INTEGER REFERENCES scenarios(id),
    transcript_json   TEXT NOT NULL,           -- [{role, content}, ...]
    disposition_json  TEXT,                    -- {intent, churn_reason, offer_made, ...}
    offer_made        TEXT,                    -- canonical accepted/presented offer string
    evidence_json     TEXT,                    -- lossless eval envelope: {offers, tool_facts, policy_decisions}
    outcome           TEXT,                    -- saved / lost / escalated
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evals (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER NOT NULL REFERENCES conversations(id),
    scores_json       TEXT NOT NULL,           -- {resolution, policy, offer_fit, tone, grounding}
    verdict           TEXT NOT NULL,           -- pass / fail / error
    rationale         TEXT,
    fairness_flag     INTEGER NOT NULL DEFAULT 0,
    rubric_version    TEXT,                     -- rubric+model that produced this grade (auditability)
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guardrail_events (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER REFERENCES conversations(id),
    type              TEXT NOT NULL,           -- jailbreak / off_scope / pii / over_limit / grounding / promise / tone
    action            TEXT NOT NULL,           -- blocked / bounded / redacted / rejected / escalated
    detail            TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER REFERENCES conversations(id),
    actor             TEXT NOT NULL,           -- agent / policy / human / system
    decision          TEXT NOT NULL,
    reason            TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
    id                INTEGER PRIMARY KEY,
    label             TEXT NOT NULL,
    summary           TEXT,
    size              INTEGER NOT NULL,
    save_rate         REAL,
    avg_margin_cost   REAL,
    example_ids_json  TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id                INTEGER PRIMARY KEY,
    theme_id          INTEGER REFERENCES themes(id),
    recommendation    TEXT NOT NULL,
    priority_score    REAL NOT NULL,
    created_at        TEXT NOT NULL
);

-- Offline health metrics that can't be derived cheaply on every read (e.g. the
-- red-team guardrail catch rate, which needs an LLM scope call per probe). The
-- red-team sweep writes the latest value here; the kill switch reads it. `version`
-- binds a result to the guardrail version that produced it, so a stale or
-- version-mismatched result can't keep authorizing normal mode after a change.
CREATE TABLE IF NOT EXISTS program_health (
    id                INTEGER PRIMARY KEY,
    metric            TEXT NOT NULL,            -- e.g. 'guardrail_catch_rate'
    value             REAL NOT NULL,
    version           TEXT,                     -- guardrail version that produced it
    detail            TEXT,
    created_at        TEXT NOT NULL
);
"""

# Tables cleared by reset_db(), in FK-safe order (children first).
_TABLES = [
    "program_health", "signals", "themes", "audit_log", "guardrail_events", "evals",
    "conversations", "scenarios", "subscriptions", "customers",
]


def record_health(conn: sqlite3.Connection, metric: str, value: float, detail: str = "",
                  version: str | None = None) -> None:
    """Append a program-health datapoint (latest wins on read), tagged with the
    guardrail version that produced it."""
    from datetime import datetime, timezone
    conn.execute(
        "INSERT INTO program_health (metric, value, version, detail, created_at) VALUES (?,?,?,?,?)",
        (metric, float(value), version, detail, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def latest_health(conn: sqlite3.Connection, metric: str) -> float | None:
    """Most-recent value for a program-health metric, or None if never recorded."""
    row = latest_health_row(conn, metric)
    return None if row is None else float(row["value"])


def latest_health_row(conn: sqlite3.Connection, metric: str):
    """Most-recent full health row (value, version, created_at), or None."""
    return conn.execute(
        "SELECT value, version, created_at FROM program_health WHERE metric=? ORDER BY id DESC LIMIT 1",
        (metric,),
    ).fetchone()


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with row access by name and FK enforcement on."""
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the initial schema shipped. CREATE TABLE IF NOT EXISTS does
# not alter an existing table, so an established keel.db needs these back-filled —
# a lightweight, idempotent migration (no migration framework for a local POC).
_ADDED_COLUMNS = [
    ("conversations", "evidence_json", "TEXT"),
    ("program_health", "version", "TEXT"),
    ("evals", "rubric_version", "TEXT"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist, then back-fill any columns added
    after the table was first created (idempotent)."""
    conn.executescript(SCHEMA)
    for table, col, decl in _ADDED_COLUMNS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Delete all rows so a re-run is reproducible (Phase 0 acceptance)."""
    init_db(conn)
    for table in _TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    # INTEGER PRIMARY KEY (no AUTOINCREMENT) restarts rowids at 1 once a table
    # is empty, so IDs are identical across runs without touching sqlite_sequence.


def dumps(obj: Any) -> str:
    """Deterministic JSON for stored blobs (sorted keys → stable across runs)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per table — used by synth/run_demo to report and self-check."""
    return {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in reversed(_TABLES)
    }
