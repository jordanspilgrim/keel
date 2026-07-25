"""Keel — end-to-end flywheel demo (handoff §9 definition of done).

The money demo. Runs the loop once, end to end, and shows it turning:

  generate  → seeded synthetic customers + scenarios
  BASELINE  → a conservative launch policy (discounts disabled); the agent can
              only offer pauses. Measure the save rate.
  learn     → grade the baseline FIRST, then rank churn segments by loss impact over
              the EVAL-ELIGIBLE conversations. NOTE: the ranking groups on the scenario's
              seeded `churn_reason` label — the embedding/KMeans clustering runs alongside
              and feeds the VoC theme cards + dashboard, but does NOT drive this selection.
              The signal: the price-sensitive segment is under-saved because discounts are off.
  ACT       → apply the recommended policy change: enable discounts and direct
              the agent to lead with a discount for price-sensitive customers.
  re-measure→ run the SAME customers again under the new policy.
  export    → dashboard/data.js, so the dashboard shows the measured lift.

The lift between BEFORE and AFTER — on identical seeded customers, changing TWO
variables together (the discount policy AND the agent playbook, both from the
analytics recommendation) — is the flywheel visibly turning. Because two variables
move together this is a paired demonstration, not an isolated causal estimate.

Run:  python run_demo.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

import batch
import config
import db
import economics
import synth
from agent import guardrails, policy, runtime
from analytics import themes
from dashboard import export
from evals import run_evals


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]

# The treated cohort is n=60. At n=20 a single customer was 5pp, so a one-run lift
# was noise-dominated (observed swings from -10pp to +35pp across runs); at n=60 one
# customer is ~1.7pp, a materially tighter — and honest — read. This needs more
# price-sensitive scenarios than the default 200-customer synth provides, so the DEMO
# seeds a larger synthetic world (DEMO_SYNTH_N); the global synth size is unchanged.
DEMO_SYNTH_N = 400
COHORT_PRICE = 60     # price-sensitive scenarios (the treated segment)
COHORT_OTHER = 20     # other churn reasons, for a representative population
WORKERS = 12
# The treated segment is SELECTED from the baseline analytics signal (see main),
# not assumed. This is the reason the discount lever is designed to address; if
# the data ever pointed elsewhere, the demo says so and bails rather than pretend.
DISCOUNT_LEVER_REASON = "Price too high"


def _segment_metrics(conn, reason: str, run_id: str | None = None, phase: str | None = None) -> dict:
    """Save rate + margin-adjusted save rate for ONE churn segment (the treated
    group) within a specific experiment run/phase — so baseline and after are
    measured on their own conversations even though both live in the same DB."""
    where = ["sc.churn_reason = ?"]
    params: list = [reason]
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    rows = conn.execute(
        # the immutable conversation-time price, not the (possibly later-changed) live
        # subscription price — margin-adjusted lift must be computed on what the customer
        # was actually paying when they were saved.
        "SELECT c.outcome, c.offer_made, COALESCE(c.price_at_conversation, s.price) AS price "
        "FROM conversations c "
        "JOIN scenarios sc ON sc.id = c.scenario_id "
        "JOIN subscriptions s ON s.customer_id = c.customer_id "
        "WHERE " + " AND ".join(where), params).fetchall()
    n = len(rows) or 1
    saved = sum(1 for r in rows if r["outcome"] == "saved")
    madj = 0.0
    for r in rows:
        if r["outcome"] == "saved":
            cost = economics.margin_cost(r["offer_made"], r["price"], accepted=True)
            madj += 1 - (cost / r["price"] if r["price"] else 0)
    return {"n": len(rows), "save_rate": round(saved / n, 4), "madj_save_rate": round(madj / n, 4)}

def _improved_system(reason: str) -> str:
    """The updated playbook the ACT step applies — parameterized by the segment the
    analytics signal selected, so the intervention tracks the data, not a constant."""
    return runtime.SYSTEM + (
        f"\n\nUPDATED PLAYBOOK (from analytics): for customers whose reason is "
        f"'{reason.lower()}', LEAD with a concrete discount offer before suggesting a pause — the "
        f"data shows discounts retain this segment materially better than pauses.")


def _cohort(conn):
    price = conn.execute("SELECT * FROM scenarios WHERE kind='churn' AND churn_reason='Price too high' "
                         "ORDER BY id LIMIT ?", (COHORT_PRICE,)).fetchall()
    other = conn.execute("SELECT * FROM scenarios WHERE kind='churn' AND churn_reason!='Price too high' "
                         "ORDER BY id LIMIT ?", (COHORT_OTHER,)).fetchall()
    return [dict(s) for s in (list(price) + list(other))]


def _redteam(conn) -> tuple[dict, float]:
    """Screen the adversarial probes; return safety counts + catch rate."""
    probes = conn.execute("SELECT opening_message, attack_type FROM scenarios WHERE is_adversarial=1").fetchall()
    counts = {"jailbreaks": 0, "off_scope": 0, "pii": 0}
    caught, total = 0, 0
    for p in probes:
        total += 1
        s = guardrails.screen_input(p["opening_message"], classify_scope=True)
        # A probe is CAUGHT if ANY layer flagged it — not only the layer matching its seeded
        # label. screen_input short-circuits scope once a jailbreak layer fires, so an
        # off_scope probe caught by the injection classifier reported off_scope=False and
        # scored as UNCAUGHT despite being BLOCKED, which is the stricter outcome. With 14
        # probes and a 0.95 floor a single crossover breaches it (0.9286) and drops the
        # program into safe mode, forcing every new session to a human. Scoring a stricter
        # catch as a miss is exactly backwards. Record which layer fired for the breakdown.
        fired = []
        if s["jailbreak"]["flagged"]:
            fired.append("jailbreaks")
        if s["off_scope"]:
            fired.append("off_scope")
        if s["pii_types"]:
            fired.append("pii")
        if fired:
            caught += 1
            # attribute to the seeded label's bucket when that layer fired, else to the
            # layer that actually caught it, so the breakdown stays truthful either way.
            seeded = {"jailbreak": "jailbreaks", "off_scope": "off_scope",
                      "pii_leak": "pii"}.get(p["attack_type"])
            counts[seeded if seeded in fired else fired[0]] += 1
    rate = caught / total if total else 0.0
    # Persist so the kill switch (safety.program_state) can gate on the latest
    # red-team result without recomputing it (an LLM call per probe) on every poll.
    # Tag it with the guardrail version so a later guardrail change invalidates it.
    db.record_health(conn, "guardrail_catch_rate", rate, f"{caught}/{total} probes caught",
                     version=guardrails.guardrail_version())
    return counts, rate


# ---------------------------------------------------------------------------
# run_once's INTEGRITY GATES, extracted as pure predicates.
#
# Line-trace instrumentation over the whole suite showed that of run_once's ~190 lines only
# the `def` executed: all five tests touching the demo stub run_once out. Mutation-testing
# confirmed the consequence — neutering the lever-compatibility bail, the identical-start
# bail, and the H5 unpaired-run abort left the suite fully green. Every integrity gate on
# the headline number could be deleted invisibly. They are pure local predicates needing no
# network, so there is no reason for them to be untestable.
# ---------------------------------------------------------------------------
def is_structurally_paired(*, cohort_ids, cohort2_ids, n_recs_a, n_recs_b, cohort_n,
                           identical_start) -> bool:
    """Both arms ran the SAME customers, both completed, and both started from a
    byte-identical world. Anything else cannot yield an honest paired lift."""
    return (cohort_ids == cohort2_ids and n_recs_a == cohort_n and n_recs_b == cohort_n
            and bool(identical_start))


def run_is_reportable(*, paired: bool, before_n: int, after_n: int, after_convs: int) -> bool:
    """H5: a run may only be reported (or counted toward a median) if it is paired, the
    treated arms match in size, and the after arm produced conversations."""
    return bool(paired) and before_n == after_n and after_convs > 0


_SNAPSHOT_COLS = ("customer_id", "plan", "price", "status", "last_save_offer_days")


def _snapshot_world(conn, customer_ids: list[int]) -> list[dict]:
    """Capture the EXACT starting subscription state for the cohort's customers. Both
    arms of the paired comparison run from this same snapshot, so eligibility (and every
    other subscription field) is held identical across arms — the ONLY things that
    differ between baseline and after are the policy and the playbook."""
    marks = ",".join("?" for _ in customer_ids)
    rows = conn.execute(
        f"SELECT {', '.join(_SNAPSHOT_COLS)} FROM subscriptions WHERE customer_id IN ({marks}) "
        f"ORDER BY customer_id", customer_ids).fetchall()
    return [dict(r) for r in rows]


def _restore_world(conn, snapshot: list[dict]) -> None:
    """Restore subscriptions to a snapshot exactly — undoing any mutation the baseline
    arm made (e.g. an accepted save sets the cooldown to 0). After this, the after arm
    starts from a world byte-identical to the one the baseline arm started from."""
    for row in snapshot:
        conn.execute(
            "UPDATE subscriptions SET plan=?, price=?, status=?, last_save_offer_days=? WHERE customer_id=?",
            (row["plan"], row["price"], row["status"], row["last_save_offer_days"], row["customer_id"]))
    conn.commit()


def _world_hash(snapshot: list[dict]) -> str:
    """A content hash of the cohort's starting subscription state. Recorded for BOTH
    arms in the manifest; identical hashes are the proof the comparison started from the
    same world, so a reader can verify eligibility wasn't changed between arms."""
    blob = json.dumps([[r[c] for c in _SNAPSHOT_COLS] for r in snapshot], sort_keys=True)
    return "world-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def run_once(conn, run_id: str | None = None) -> dict | None:
    """Run the full flywheel ONCE and return its manifest — or None on a *structural*
    bail (a lever-incompatible signal, or a non-identical starting state). A completed
    paired run is returned REGARDLESS of the lift's sign: the median-of-k estimator must
    include the low, zero, and negative draws, or it would be cherry-picking."""
    run_id = run_id or ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    print(f"① generate — seeded synthetic customers  (run {run_id})")
    synth.generate(conn, n=DEMO_SYNTH_N)  # larger world so the treated cohort can be n=60
    cohort = _cohort(conn)
    cohort_ids = sorted(s["id"] for s in cohort)
    customer_ids = [s["customer_id"] for s in cohort]
    # Capture the EXACT starting world. Both arms run from this snapshot, so eligibility
    # is held constant — the only things that differ are the policy and the playbook.
    world0 = _snapshot_world(conn, customer_ids)
    world_sha = _world_hash(world0)
    print(f"   cohort: {len(cohort)} customers ({COHORT_PRICE} price-sensitive + {COHORT_OTHER} other)  ·  "
          f"starting-state {world_sha}\n")

    # ---- BEFORE: conservative policy, discounts disabled --------------------
    print("② BASELINE — discounts DISABLED (conservative launch policy)")
    policy.DISCOUNTS_ENABLED = False
    recs_a = batch.run_batch(conn, cohort, system=runtime.SYSTEM, max_workers=WORKERS,
                             run_id=run_id, phase="baseline")
    before = export.conversation_metrics(conn, run_id=run_id, phase="baseline")

    # ---- MEASURE GATES LEARN (H1): grade the BASELINE arm BEFORE any analytics, so the
    # intervention is chosen only from conversations that PASSED the current eval spec — an
    # ungraded/failed/hallucinating baseline conversation cannot steer the flywheel.
    print("   grading the baseline arm (Measure) before Learn…")
    run_evals.grade_all(conn, run_id=run_id, phase="baseline")

    # ---- LEARN: cluster the VoC, then CONSUME an eval-governed intervention signal --
    themes.run_analytics(conn)  # embed → cluster → theme cards → ranked signals (persisted)
    signal = themes.recommend_intervention(  # eval-eligible baseline only (H1)
        conn, run_id=run_id, phase="baseline", eligible_only=True)
    print("③ LEARN — structured intervention signal (from eval-eligible baseline conversations):")
    _el = signal.get("eval_eligibility") or {}
    if _el:
        print(f"     eligibility: {_el['eligible']}/{_el['total']} baseline conversations passed the "
              f"current eval spec (coverage {_el['coverage']*100:.0f}%); rule: {_el['rule']}")
    # Measure gates Learn: with ZERO eval-eligible baseline conversations there is nothing
    # honest to choose an intervention from. That state used to crash with a KeyError deep in
    # the ranking rather than bail — the demo died exactly when its own gate was doing its
    # job, which reads as a bug instead of a designed refusal.
    if _el and _el.get("eligible", 0) == 0:
        print(f"\n   ⚠ no baseline conversation passed the current eval spec "
              f"(0/{_el.get('total', 0)}) — Measure gates Learn, so there is no eligible "
              f"evidence to choose an intervention from. Aborting this run.")
        return None
    for s in signal["evidence"].get("segment_ranking", []):
        print(f"     {s['reason']:<22} save {s['save_rate']*100:>3.0f}%  ·  n={s['n']}  ·  loss={s['loss']}")
    print(f"   signal: segment='{signal['segment']}' · lever={signal['recommended_lever']} · "
          f"confidence={signal['confidence']} · action: {signal['recommended_action']}")
    # Act consumes the signal. If analytics recommends a segment no available lever
    # fits, the demo BAILS rather than misattribute a lift to the wrong lever.
    if not signal["lever_compatible"]:
        print(f"\n   ⚠ no available policy lever fits '{signal['segment']}' — the honest move is a "
              f"different intervention. Demo bails rather than misattribute.")
        return None
    target = signal["segment"]
    before_seg = _segment_metrics(conn, target, run_id, "baseline")
    # Persist the signal under this run_id and load it back by id, so Act consumes
    # the signal THROUGH the datastore (durable Learn→Act lineage).
    signal_id = themes.persist_signal(conn, signal, run_id=run_id)
    signal = themes.load_signal(conn, signal_id)
    target = signal["segment"]

    # ---- ACT: enable discounts + improved playbook (TWO variables) ----------
    # RESTORE the exact starting world so the after arm begins byte-identical to the
    # baseline arm — undoing any subscription mutation baseline made (e.g. an accepted
    # save sets the cooldown to 0). This is what makes eligibility a held constant
    # rather than a hidden confound: the only variables that change are policy + playbook.
    improved_system = _improved_system(target)
    print(f"④ ACT — consuming persisted signal #{signal_id}: enable discounts + lead-with-discount "
          f"playbook for the '{target}' segment")
    policy.DISCOUNTS_ENABLED = True
    _restore_world(conn, world0)
    world_after_sha = _world_hash(_snapshot_world(conn, customer_ids))
    identical_start = world_after_sha == world_sha  # proof the after arm starts from the same world
    if not identical_start:
        print(f"   ⚠ starting-state mismatch ({world_after_sha} != {world_sha}) — the comparison would "
              f"not be apples-to-apples. Demo bails rather than report a confounded lift.")
        return None
    print(f"   restored starting-state {world_after_sha} (identical to baseline) — eligibility held constant")
    cohort2 = _cohort(conn)  # SAME scenarios (not re-seeded) → paired by construction
    cohort2_ids = sorted(s["id"] for s in cohort2)

    print("⑤ RE-MEASURE — the same customers under the new policy + playbook")
    recs_b = batch.run_batch(conn, cohort2, system=improved_system, max_workers=WORKERS,
                             run_id=run_id, phase="after")
    after = export.conversation_metrics(conn, run_id=run_id, phase="after")
    after_seg = _segment_metrics(conn, target, run_id, "after")
    print(f"   overall save {after['save_rate']*100:.0f}%  ·  price-sensitive save {after_seg['save_rate']*100:.0f}%")

    # Grade the AFTER arm (the baseline was already graded before Learn); together the two
    # phase-scoped grades cover every conversation exactly once. The manifest's "after"
    # eval metrics therefore describe the after arm specifically.
    print("   grading the after arm + re-clustering…")
    m = run_evals.grade_all(conn, run_id=run_id, phase="after")
    themes.run_analytics(conn)
    counts, catch_rate = _redteam(conn)
    # over_limit now means ONLY a genuine over-ceiling proposal that policy capped or
    # rejected. Policy-disabled blocks (the baseline arm's own DISCOUNTS_ENABLED switch) and
    # invalid arguments get their own counts rather than inflating the safety tile.
    for _t in ("over_limit", "policy_disabled", "invalid_args"):
        counts[_t] = conn.execute(
            "SELECT count(*) FROM guardrail_events WHERE type=?", (_t,)).fetchone()[0]

    # Headline = the TREATED segment (where the discount act applies). Overall is
    # reported as context — it mixes in the 20 untreated customers and is noisier.
    seg_lift = (after_seg["save_rate"] - before_seg["save_rate"]) * 100
    seg_madj = (after_seg["madj_save_rate"] - before_seg["madj_save_rate"]) * 100
    overall_lift = (after["save_rate"] - before["save_rate"]) * 100
    paired = is_structurally_paired(
        cohort_ids=cohort_ids, cohort2_ids=cohort2_ids, n_recs_a=len(recs_a),
        n_recs_b=len(recs_b), cohort_n=len(cohort), identical_start=identical_start)
    # H5: an UNPAIRED run (cohort mismatch, arm-count mismatch, treated-n mismatch, or zero
    # coverage) is STRUCTURALLY invalid — it cannot yield an honest paired lift. Abort it
    # (return None) rather than write artifacts or let it count toward a median; the demo's
    # own definition of done requires a matched pair.
    if not run_is_reportable(paired=paired, before_n=before_seg["n"],
                             after_n=after_seg["n"], after_convs=after["n"]):
        print(f"\n   ⚠ structural failure (paired={paired}, treated_n {before_seg['n']}/{after_seg['n']}, "
              f"after_convs {after['n']}) — aborting this run rather than report a "
              f"confounded or empty result.")
        return None

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "cohort_size": len(cohort), "cohort_scenario_ids": cohort_ids, "paired_cohort": paired,
        # The TREATED segment's n (== both arms; the paired demo runs the same customers),
        # so every doc that cites "treated cohort n=…" is backed by this artifact (M6).
        "treated_cohort_n": before_seg["n"],
        "treated_segment": target,
        # Honest mechanism label: the ranking groups on the scenario's SEEDED churn_reason
        # label (not the embedding/KMeans clusters, which feed the VoC cards + dashboard only).
        "segment_selection": ("loss-ranked over the seeded churn_reason label, gated on "
                              "current-spec eval-eligible baseline conversations; consumed by "
                              "Act via the persisted signal id (clustering does NOT drive it)"),
        "intervention_signal_id": signal_id,
        "intervention_signal": signal,
        "guardrail_version": guardrails.guardrail_version(),
        "variables_changed": ["discount_policy", "agent_playbook"],
        "held_constant": ["cohort_scenarios", "starting_subscription_state", "eligibility"],
        "identical_starting_state": identical_start,
        "starting_state_sha": world_sha,
        "models": {"flagship": config.FLAGSHIP_MODEL, "mini": config.MINI_MODEL, "embedding": config.EMBEDDING_MODEL},
        "baseline": {"policy": "discounts_disabled", "playbook_sha": _sha(runtime.SYSTEM), "conversations": len(recs_a),
                     "starting_state_sha": world_sha,
                     "segment_save_rate": before_seg["save_rate"], "overall_save_rate": before["save_rate"]},
        "after": {"policy": "discounts_enabled", "playbook_sha": _sha(improved_system), "conversations": len(recs_b),
                  "starting_state_sha": world_after_sha,
                  "segment_save_rate": after_seg["save_rate"], "overall_save_rate": after["save_rate"],
                  "eval_pass_rate": m["eval_pass_rate"], "eval_coverage": m["coverage"]},
        "lift": {"segment_save_pp": round(seg_lift, 1), "segment_madj_pp": round(seg_madj, 1),
                 "overall_save_pp": round(overall_lift, 1)},
        "guardrail_catch_rate": round(catch_rate, 3),
        # OBSERVATIONAL outcome parity across seeded groups (NOT agent-treatment fairness —
        # the agent never sees the attribute). Named honestly so the dashboard/docs can't
        # present sampling noise as a fairness guarantee (M2). Real agent-treatment fairness
        # is the counterfactual harness in evals/agent_fairness.py.
        "outcome_parity_gap": m["outcome_parity_gap"],
        "outcome_parity_observational": True,
        "note": ("Paired before/after on identical seeded customers run from a byte-identical starting "
                 "subscription state (same starting_state_sha in both arms → eligibility held constant). "
                 "TWO variables changed together (discount policy + agent playbook), so this is a synthetic "
                 "PAIRED demonstration of the flywheel on the treated (price-sensitive) segment — not an "
                 "isolated causal estimate. Numbers vary run to run."),
    }
    with open("dashboard/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    # Retain a copy of every run's manifest, so any range/consistency claim in the docs is
    # backed by a persisted artifact (not just prose). Name it by RUN_ID, not by finish time:
    # stamping the filename with generated_at meant sequential runs' names were offset by one
    # run, so the file named after the committed run actually held a DIFFERENT run's numbers —
    # a verifier indexing by filename would read the wrong result.
    os.makedirs("dashboard/manifests", exist_ok=True)
    with open(f"dashboard/manifests/manifest-{manifest['run_id']}.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("⑥ EXPORT — writing dashboard/data.js + dashboard/manifest.json")
    # dashboard trend shows the TREATED segment before/after — what the act moved
    data = export.export(conn, before=before_seg, after=after_seg, guardrail_counts=counts,
                         catch_rate=catch_rate, provenance=manifest)

    print("\n" + "=" * 72)
    print("FLYWHEEL RESULT  (synthetic paired demo — treated price-sensitive segment)")
    print(f"  price-sensitive save:   {before_seg['save_rate']*100:>5.0f}%  →  {after_seg['save_rate']*100:>5.0f}%   ({seg_lift:+.0f} pp)")
    print(f"  price-sensitive m-adj:  {before_seg['madj_save_rate']*100:>5.0f}%  →  {after_seg['madj_save_rate']*100:>5.0f}%   ({seg_madj:+.1f} pp)")
    print(f"  overall (context):      {before['save_rate']*100:>5.0f}%  →  {after['save_rate']*100:>5.0f}%   ({overall_lift:+.0f} pp)")
    print(f"  eval pass rate:       {m['eval_pass_rate']*100:.0f}% (coverage {m['coverage']*100:.0f}%)   ·   "
          f"guardrail catch rate: {catch_rate*100:.0f}%   ·   compliance: {data['kpis']['compliance_coverage']*100:.0f}%")
    print(f"  outcome parity gap:   {m['outcome_parity_gap']} (observational)   ·   paired cohort: {paired}   ·   manifest: dashboard/manifest.json")
    print("=" * 72)

    ok = paired and seg_lift > 0 and data["meta"]["conversations"] > 0
    print("\nDEMO:", "the flywheel turned — treated-segment lift positive, paired cohort, provenance recorded." if ok
          else "did NOT meet the bar (needs a matched paired cohort AND a strictly positive treated-segment lift).")
    # NOTE: a non-positive lift is still a valid draw and is RETURNED (only the single-run
    # main() translates it to a nonzero exit code). The median-of-k estimator must see it.
    # Return the export `data` alongside the manifest: in --median mode each run resets the
    # DB, so the committed run's rows are gone by the end — the ONLY way to re-render the
    # dashboard from the committed (median) run is to keep its export payload in memory.
    return {"manifest": manifest, "data": data}


def main() -> int:
    """Single honest run (kept for `python run_demo.py`)."""
    conn = db.connect()
    try:
        ret = run_once(conn)
    finally:
        conn.close()
    if ret is None:
        return 1
    manifest = ret["manifest"]
    return 0 if (manifest["paired_cohort"] and manifest["lift"]["segment_save_pp"] > 0) else 1


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else round((s[mid - 1] + s[mid]) / 2, 3)


def run_median(k: int = 5) -> int:
    """PRE-REGISTERED median-of-k: run the flywheel k times (fixed seed → same world, so
    the only thing that varies is the LLM draw — exactly the run-to-run variance a user
    re-running the demo sees), then report the MEDIAN treated-segment lift and the FULL
    range. k is fixed in advance; every run is included (no dropping low/negative draws);
    the committed headline is the median run, never the max. This is the honest fix for a
    high-variance single-run estimator — not cherry-picking. If any run bails structurally
    the whole estimate aborts (no partial median over fewer than k)."""
    # k MUST be odd: the committed run is the real run whose lift EQUALS the median, which
    # only exists for odd k. For even k the median is interpolated (a value no single run
    # need equal), so manifest.json (a real committed run) would disagree with the reported
    # aggregate median. Reject rather than silently ship divergent artifacts.
    # k < 3 is not a variance-controlled estimate. --k=1 used to pass every guard, print
    # "PRE-REGISTERED MEDIAN-OF-1", exit 0, and write demo_aggregate.json whose `method`
    # string claims "the committed headline is the median run, not the max" over a sample
    # of ONE — satisfying every assertion in the median test vacuously.
    if k % 2 == 0 or k < 3:
        raise SystemExit(f"--k must be an ODD integer >= 3 (got {k}); an even k has no real "
                         f"median run, and k<3 is a single draw dressed as a variance-"
                         f"controlled estimate")
    conn = db.connect()
    runs: list[dict] = []          # each is {"manifest": ..., "data": ...} from run_once
    snap_dir = tempfile.mkdtemp(prefix="keel-demo-db-")  # ephemeral per-run DB snapshots (H5)
    db_snapshots: dict[str, str] = {}    # run_id -> snapshot db path
    snapshot_errors: list[str] = []
    _restore_failed = False

    def _snapshot_db(run_id: str) -> None:
        # Snapshot the just-finished run's DB before the next run resets it, so the committed
        # (median) run can be restored as the canonical Explorer/API database — otherwise the
        # live surfaces would read whichever run happened to execute LAST (H5). Best-effort.
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            snap = os.path.join(snap_dir, f"{run_id}.db")
            shutil.copy(config.DB_PATH, snap)
            db_snapshots[run_id] = snap
        except Exception as e:
            # Recorded, not swallowed. If the committed run's snapshot is missing, the
            # restore below is skipped and keel.db ends up holding whichever run executed
            # LAST while every artifact names the committed median run — the dashboard and
            # the live Explorer/API would then disagree, silently, with exit code 0.
            snapshot_errors.append(f"{run_id}: {type(e).__name__}: {e}")

    try:
        for i in range(k):
            print(f"\n{'#' * 20} PRE-REGISTERED RUN {i + 1}/{k} {'#' * 20}")
            ret = run_once(conn)
            if ret is None:
                raise SystemExit(f"run {i + 1}/{k} bailed structurally — aborting the median "
                                 f"(an honest estimate cannot be computed over fewer than k runs)")
            runs.append(ret)
            _snapshot_db(ret["manifest"]["run_id"])
    finally:
        conn.close()

    manifests = [r["manifest"] for r in runs]

    def _dist(path_a: str, path_b: str) -> dict:
        vals = [mf[path_a][path_b] for mf in manifests]
        return {"median": _median(vals), "min": min(vals), "max": max(vals), "values": vals}

    seg_vals = [mf["lift"]["segment_save_pp"] for mf in manifests]
    median_seg = _median(seg_vals)
    # The committed run is a REAL run whose segment lift equals the median (odd k → exists),
    # so dashboard/manifest.json stays a single self-consistent artifact that IS the median.
    committed_ret = min(runs, key=lambda r: abs(r["manifest"]["lift"]["segment_save_pp"] - median_seg))
    committed = committed_ret["manifest"]
    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": ("pre-registered median of k paired runs; fixed seed (identical world), so the "
                   "variance measured is the LLM-draw variance a re-run actually sees; every run "
                   "included; committed headline is the median run, not the max"),
        "k": k, "seed": config.SYNTH_SEED, "treated_cohort_n": committed["treated_cohort_n"],
        "run_ids": [mf["run_id"] for mf in manifests],
        "committed_run_id": committed["run_id"],
        "segment_save_pp": _dist("lift", "segment_save_pp"),
        "segment_madj_pp": _dist("lift", "segment_madj_pp"),
        "overall_save_pp": _dist("lift", "overall_save_pp"),
        "eval_pass_rate": {"median": _median([mf["after"]["eval_pass_rate"] for mf in manifests]),
                           "min": min(mf["after"]["eval_pass_rate"] for mf in manifests),
                           "max": max(mf["after"]["eval_pass_rate"] for mf in manifests)},
        "outcome_parity_gap": {"median": _median([mf["outcome_parity_gap"] for mf in manifests]),
                               "min": min(mf["outcome_parity_gap"] for mf in manifests),
                               "max": max(mf["outcome_parity_gap"] for mf in manifests),
                               "observational": True},
    }
    with open("dashboard/demo_aggregate.json", "w") as f:
        json.dump(aggregate, f, indent=2)
    # Make dashboard/manifest.json the committed (median) run — a real, self-consistent
    # artifact equal to the reported median headline.
    with open("dashboard/manifest.json", "w") as f:
        json.dump(committed, f, indent=2)
    # Re-render the dashboard's data.js/data.json from the COMMITTED (median) run's export
    # payload. Without this the dashboard would show whatever run happened to run LAST (its
    # rows are the only ones left in the DB after the final reset) — which would let the
    # flagship visible surface display an above-median lift while the docs claim the median.
    export.write_data(committed_ret["data"])
    # Restore the committed (median) run's DB as the CANONICAL Explorer/API database (H5), so
    # the live surfaces describe the SAME run the dashboard renders — not the last one run.
    committed_snap = db_snapshots.get(committed["run_id"])
    if committed_snap and os.path.exists(committed_snap):
        # Copy to a sidecar and os.replace() it into place. The old code unlinked keel.db and
        # its -wal/-shm siblings BEFORE copying, so an interruption or a full disk left no
        # database at all — and db.connect silently recreates an empty one, so the failure
        # surfaced only later as a 500. os.replace is atomic on the same filesystem.
        tmp = config.DB_PATH + ".restore"
        shutil.copy(committed_snap, tmp)
        for ext in ("-wal", "-shm"):
            try:
                os.remove(config.DB_PATH + ext)
            except OSError:
                pass
        os.replace(tmp, config.DB_PATH)
    else:
        # LOUD, and nonzero. Silently skipping the restore means keel.db holds a DIFFERENT
        # run from the one every artifact names.
        print(f"\n   ⚠ could not restore the committed run's database "
              f"(snapshot missing for {committed['run_id']}). keel.db now holds whichever run "
              f"executed LAST, so the live Explorer/API will DISAGREE with the committed "
              f"dashboard artifacts. Snapshot errors: {snapshot_errors or 'none recorded'}")
        _restore_failed = True
    shutil.rmtree(snap_dir, ignore_errors=True)

    sp = aggregate["segment_save_pp"]
    print("\n" + "=" * 72)
    print(f"PRE-REGISTERED MEDIAN-OF-{k}  (treated price-sensitive segment)")
    print(f"  segment save lift:   median {sp['median']:+.1f} pp   range [{sp['min']:+.1f}, {sp['max']:+.1f}] pp   "
          f"values {[round(v, 1) for v in sp['values']]}")
    print(f"  committed run:       {committed['run_id']}  "
          f"({round(committed['baseline']['segment_save_rate']*100)}% → "
          f"{round(committed['after']['segment_save_rate']*100)}%)")
    print(f"  eval pass (median):  {aggregate['eval_pass_rate']['median']*100:.0f}%   ·   "
          f"outcome parity gap (median, observational): {aggregate['outcome_parity_gap']['median']}")
    print(f"  aggregate: dashboard/demo_aggregate.json   ·   committed manifest: dashboard/manifest.json")
    # H5: the demo's own definition of done requires a STRICTLY POSITIVE treated-segment
    # lift — return nonzero when the median is not positive, so CI / a demo script can't read
    # a flat or negative flywheel as success.
    # A failed DB restore is ALSO a failure: the artifacts and the live surfaces would
    # describe different runs, which is a correctness problem regardless of the lift.
    ok = sp["median"] > 0 and not _restore_failed
    print(f"  RESULT: {'the flywheel turned (median lift positive)' if ok else 'did NOT meet the bar'}"
          f"{' — DB RESTORE FAILED (artifacts and live surfaces disagree)' if _restore_failed else ''}")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--median" in sys.argv:
        n = 5
        for a in sys.argv:
            if a.startswith("--k="):
                n = int(a.split("=", 1)[1])
        sys.exit(run_median(n))
    sys.exit(main())
