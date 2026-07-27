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
    run_id            TEXT,                    -- experiment run this belongs to (flywheel lineage)
    phase             TEXT,                    -- baseline / after (within a run)
    resolution_key    TEXT,                    -- durable idempotency key for a live resolve (NULL for batch)
    price_at_conversation REAL,                -- subscription price snapshot; historical margin is immutable to later price changes
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evals (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER NOT NULL REFERENCES conversations(id),
    scores_json       TEXT NOT NULL,           -- {resolution, policy, offer_fit, tone, grounding}
    verdict           TEXT NOT NULL,           -- pass / fail / error
    rationale         TEXT,
    fairness_flag     INTEGER NOT NULL DEFAULT 0,
    rubric_version    TEXT,                     -- content-hashed eval-spec id (rubric+schema+model+evidence)
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

CREATE TABLE IF NOT EXISTS cancellation_requests (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER REFERENCES conversations(id),  -- linked at persist; NULL when queued live
    session_key       TEXT,                    -- durable idempotency key when routed live, before persist
    status            TEXT NOT NULL,           -- pending_human (mock work queue)
    channel           TEXT,                    -- follow-up channel the reply promised, e.g. email
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalation_requests (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER REFERENCES conversations(id),  -- linked at persist; NULL when queued live
    session_key       TEXT,                    -- durable idempotency key when escalated live, before persist
    reason            TEXT,                    -- why the hand-off happened
    status            TEXT NOT NULL,           -- pending_human (mock hand-off queue)
    created_at        TEXT NOT NULL
);

-- M2/M3: a durable mock work-queue for APPLYING an accepted retention offer. A save says
-- "our team will apply it" — that promise is only true if a real pending action exists, so
-- an accepted offer queues a fulfillment record here (the analogue of the cancellation /
-- escalation queues). Nothing auto-applies in the POC; this is the honest hand-off.
CREATE TABLE IF NOT EXISTS offer_fulfillment_requests (
    id                INTEGER PRIMARY KEY,
    conversation_id   INTEGER REFERENCES conversations(id),   -- NULL until persist links it
    offer             TEXT NOT NULL,           -- the accepted offer, e.g. '3-month pause' / '20% discount'
    status            TEXT NOT NULL,           -- pending_application (mock work queue)
    session_key       TEXT,                    -- durable idempotency key when accepted live, before persist
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
    run_id            TEXT,                    -- experiment run this signal drove (lineage)
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
    "offer_fulfillment_requests", "cancellation_requests", "escalation_requests",
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
    ("conversations", "run_id", "TEXT"),
    ("conversations", "phase", "TEXT"),
    ("program_health", "version", "TEXT"),
    ("evals", "rubric_version", "TEXT"),
    ("signals", "run_id", "TEXT"),
    ("conversations", "resolution_key", "TEXT"),
    ("conversations", "price_at_conversation", "REAL"),
    ("cancellation_requests", "session_key", "TEXT"),
    # M1: an existing DB whose escalation_requests table predates session_key must gain the
    # column BEFORE idx_escalation_session_key is created, or init_db fails with
    # "no such column: session_key". Fresh DBs already include it via CREATE TABLE.
    ("escalation_requests", "session_key", "TEXT"),
    # An earned SAVE now pre-writes its fulfillment row at turn time like the other two
    # terminals, so this table needs the same durable idempotency key on existing DBs.
    ("offer_fulfillment_requests", "session_key", "TEXT"),
    # Live guardrail/audit telemetry is pre-written at TURN time keyed by session, so a
    # blocked jailbreak is durable the moment it happens rather than only if the customer
    # later chooses to finalize. Existing DBs need the column.
    ("guardrail_events", "session_key", "TEXT"),
    ("audit_log", "session_key", "TEXT"),
]


def _relax_not_null_for_pre_persist_queues(conn: sqlite3.Connection) -> None:
    """Make `offer_fulfillment_requests.conversation_id` nullable on legacy databases.

    The R12 write-before-promise change enqueues an accepted offer at TURN time, keyed by
    session, BEFORE the conversation row exists — so conversation_id must be NULL initially.
    Fresh databases get that from CREATE TABLE, but ALTER TABLE ADD COLUMN cannot relax an
    existing NOT NULL, so on any pre-R12 database the surviving constraint rejected the
    pre-write. The insert used INSERT OR IGNORE (correct for its session-key uniqueness
    guard) which SWALLOWS that rejection too, so the durable write was a silent no-op and
    the customer received "our team will apply it" with nothing backing it. The migration
    added the column; it did not add the durability.

    SQLite cannot ALTER a column's nullability, so rebuild the table when needed. Guarded on
    the actual constraint, so it is a no-op on current schemas and safe to re-run."""
    info = list(conn.execute("PRAGMA table_info(offer_fulfillment_requests)"))
    if not info:
        return
    conv = next((r for r in info if r["name"] == "conversation_id"), None)
    if conv is None or not conv["notnull"]:
        return  # already nullable — nothing to do
    # Re-entrant and atomic. executescript() autocommits per statement, so a crash
    # after DROP stranded rows in _ofr_rebuild and a crash BEFORE it made every subsequent
    # init_db raise "table _ofr_rebuild already exists" permanently. Drop any leftover first,
    # and wrap the move in one transaction. PRAGMA foreign_keys is toggled OUTSIDE the
    # transaction because it is a no-op inside one.
    conn.execute("DROP TABLE IF EXISTS _ofr_rebuild")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # Individual execute() calls, NOT executescript(). sqlite3.executescript() issues an
        # implicit COMMIT before running, which silently commits away the BEGIN IMMEDIATE this
        # block is wrapped in — so the previous version was documented and committed as
        # "atomic ... one transaction" while actually running in autocommit, and a crash
        # between DROP and RENAME would have left no table at all. Verified: in_transaction
        # goes True -> False across an executescript call.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE _ofr_rebuild (
                id                INTEGER PRIMARY KEY,
                conversation_id   INTEGER REFERENCES conversations(id),
                offer             TEXT NOT NULL,
                status            TEXT NOT NULL,
                session_key       TEXT,
                created_at        TEXT NOT NULL
            )""")
        conn.execute("""
            INSERT INTO _ofr_rebuild (id, conversation_id, offer, status, session_key, created_at)
                SELECT id, conversation_id, offer, status, session_key, created_at
                FROM offer_fulfillment_requests""")
        conn.execute("DROP TABLE offer_fulfillment_requests")
        conn.execute("ALTER TABLE _ofr_rebuild RENAME TO offer_fulfillment_requests")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist, back-fill any columns added after the
    table was first created, and (idempotently) enforce one grade per
    (conversation, eval-spec version). All steps are safe to re-run."""
    conn.executescript(SCHEMA)
    for table, col, decl in _ADDED_COLUMNS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    _relax_not_null_for_pre_persist_queues(conn)
    # Dedup any pre-existing duplicate grades (keep the newest id) BEFORE adding the
    # unique index, so the migration never fails on legacy data.
    conn.execute(
        "DELETE FROM evals WHERE id NOT IN ("
        "  SELECT MAX(id) FROM evals GROUP BY conversation_id, rubric_version)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evals_conv_spec "
        "ON evals(conversation_id, rubric_version)")
    # Durable idempotency for a live resolve: at most one conversation per resolution
    # key. Partial index so batch conversations (NULL key) are unconstrained. A double
    # resolve of the same session hits this at the DB level even across a restart.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_resolution_key "
        "ON conversations(resolution_key) WHERE resolution_key IS NOT NULL")
    # At most one live-queued cancellation per session (idempotent turn-time routing).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cancel_session_key "
        "ON cancellation_requests(session_key) WHERE session_key IS NOT NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_escalation_session_key "
        "ON escalation_requests(session_key) WHERE session_key IS NOT NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_fulfillment_session_key "
        "ON offer_fulfillment_requests(session_key) WHERE session_key IS NOT NULL")
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
