"""Concurrent conversation batch runner.

Simulating a conversation is LLM-bound (many round-trips), so batches run the
simulations concurrently — each worker with its OWN read connection — then
persist sequentially on the main connection. SQLite tolerates concurrent
readers; centralizing writes avoids "database is locked". A worker that raises
is dropped (logged), not fatal to the batch.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import db
from agent import runtime

MAX_WORKERS = 8


def _conn_path(conn) -> str | None:
    """The file backing the caller's connection, so workers read the SAME DB
    (not the global config.DB_PATH). Returns None for an in-memory / unnamed DB."""
    for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file or None
    return None


def run_batch(conn, scenarios, *, system: str = runtime.SYSTEM, max_workers: int = MAX_WORKERS,
              progress: bool = True, db_path: str | None = None) -> list[dict]:
    """Simulate all scenarios concurrently, persist sequentially, return records.

    Workers open their own read connection to the SAME database as `conn` (derived
    from it, or overridden via `db_path`) — not the global default — so a batch
    run against a non-default DB reads the right data."""
    scenarios = [dict(s) for s in scenarios]
    worker_path = db_path or _conn_path(conn)

    def work(scn):
        c = db.connect(worker_path)  # per-thread read connection, same DB as caller
        try:
            return runtime.simulate_conversation(scn, c, system=system)
        except Exception as e:  # one bad conversation shouldn't sink the batch
            return {"_error": f"{type(e).__name__}: {e}", "scenario_id": scn.get("id")}
        finally:
            c.close()

    records, errors = [], 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, rec in enumerate(ex.map(work, scenarios), 1):
            if rec.get("_error"):
                errors += 1
            else:
                records.append(rec)
            if progress and i % 5 == 0:
                print(f"    …{i}/{len(scenarios)} simulated ({errors} errors)")

    for rec in records:  # centralized writes on the main connection
        runtime.persist_conversation(conn, rec)
    if errors:
        print(f"    batch: {errors}/{len(scenarios)} conversations errored and were dropped")
    return records
