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


def run_batch(conn, scenarios, *, system: str = runtime.SYSTEM, max_workers: int = MAX_WORKERS,
              progress: bool = True) -> list[dict]:
    """Simulate all scenarios concurrently, persist sequentially, return records."""
    scenarios = [dict(s) for s in scenarios]

    def work(scn):
        c = db.connect()  # per-thread read connection
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
