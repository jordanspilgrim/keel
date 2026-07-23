"""Regression tests for the money-demo integrity properties the sixth review flagged:
- H3: both arms of the paired comparison must run from a byte-identical starting world
  (snapshot → restore → identical hash), so eligibility is a held constant, not a
  hidden confound.
- M2: the run-scoped intervention signal the manifest cites must survive a SECOND
  analytics pass — the post-run re-cluster must not delete it.
No network: these exercise the deterministic snapshot/restore + signal-lineage plumbing.
"""

from __future__ import annotations

import pytest

import db
import synth
import run_demo
from analytics import themes


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    synth.generate(c)
    yield c
    c.close()


# --- H3: identical starting world across arms ------------------------------
def test_restore_world_is_byte_identical(conn):
    customer_ids = [r["customer_id"] for r in
                    conn.execute("SELECT customer_id FROM subscriptions ORDER BY customer_id LIMIT 30").fetchall()]
    world0 = run_demo._snapshot_world(conn, customer_ids)
    sha0 = run_demo._world_hash(world0)

    # simulate what the BASELINE arm can do: an accepted save resets a customer's
    # cooldown to 0, and a seeded-ineligible customer would otherwise stay changed
    conn.execute("UPDATE subscriptions SET last_save_offer_days = 0 WHERE customer_id = ?", (customer_ids[0],))
    conn.commit()
    assert run_demo._world_hash(run_demo._snapshot_world(conn, customer_ids)) != sha0  # world moved

    # restoring the snapshot returns the world to byte-identical starting state
    run_demo._restore_world(conn, world0)
    sha_after = run_demo._world_hash(run_demo._snapshot_world(conn, customer_ids))
    assert sha_after == sha0  # the after arm starts from the same world the baseline arm did
    restored = conn.execute("SELECT last_save_offer_days FROM subscriptions WHERE customer_id=?",
                            (customer_ids[0],)).fetchone()["last_save_offer_days"]
    assert restored == world0[0]["last_save_offer_days"]  # the mutation was undone exactly


def test_world_hash_is_order_independent_and_reproducible(conn):
    ids = [r["customer_id"] for r in
           conn.execute("SELECT customer_id FROM subscriptions ORDER BY customer_id LIMIT 10").fetchall()]
    # snapshot orders by customer_id, so two snapshots of the same world hash identically
    assert run_demo._world_hash(run_demo._snapshot_world(conn, ids)) \
        == run_demo._world_hash(run_demo._snapshot_world(conn, list(reversed(ids))))


# --- M2: cited experiment signal survives a post-run re-cluster ------------
def test_experiment_signal_survives_reclustering(conn):
    signal = {"segment": "Price too high", "recommended_lever": "discount", "evidence": {"loss": 4.2}}
    sid = themes.persist_signal(conn, signal, run_id="run-TEST")
    assert themes.load_signal(conn, sid) is not None

    # the post-run re-cluster writes fresh EPHEMERAL dashboard signals (run_id NULL)…
    themes.persist(conn,
                   [{"label": "L", "summary": "s", "size": 3, "save_rate": 0.5,
                     "avg_margin_cost": 1.0, "example_ids": [1]}],
                   [{"label": "L", "recommendation": "r", "priority_score": 1.5}])

    # …and the run-scoped experiment signal the manifest cites is STILL deref-able
    reloaded = themes.load_signal(conn, sid)
    assert reloaded is not None and reloaded["segment"] == "Price too high"
    assert conn.execute("SELECT count(*) FROM signals WHERE run_id IS NULL").fetchone()[0] >= 1
    assert conn.execute("SELECT count(*) FROM signals WHERE run_id='run-TEST'").fetchone()[0] == 1
