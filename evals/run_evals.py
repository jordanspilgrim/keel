"""Eval runners (plan §3c, §8, Phase 3).

grade_all()  — judge every conversation in the DB, write evals rows, report the
               eval pass rate, hallucination rate, and the fairness slice.
run_golden() — judge the hand-labeled golden set and report judge-vs-human
               agreement (the judge's calibration / regression guard).
"""

from __future__ import annotations

import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import config
import db
from evals import judge

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
AGREEMENT_FLOOR = 0.8       # judge-vs-human VERDICT agreement must hold at or above this
CALIBRATION_MAE_FLOOR = 1.0  # mean per-dimension |judge - human| must be within this


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# build_judge_input lives in judge.py (it is part of the grade DEFINITION the eval-spec
# hash covers). Re-exported here so run_evals.build_judge_input keeps working.
build_judge_input = judge.build_judge_input


def grade_all(conn, *, max_workers: int = 8, run_id: str | None = None, phase: str | None = None) -> dict:
    """Judge every conversation from the persisted eval envelope; persist an eval
    row for EACH one — a judge failure records verdict='error' so coverage stays
    honest, never a silent drop. Verdicts are derived mechanically from the scores.

    Optionally scoped to a run_id/phase so the money demo can grade the BASELINE arm
    BEFORE analytics selects an intervention (H1) — without re-judging it again when the
    after arm is graded. The returned metrics then describe only the scoped subset."""
    where, params = [], []
    if run_id is not None:
        where.append("run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("phase = ?"); params.append(phase)
    q = "SELECT id FROM conversations" + (" WHERE " + " AND ".join(where) if where else "")
    ids = [r["id"] for r in conn.execute(q, params).fetchall()]
    # Build each judge input on the MAIN thread (SQLite is not thread-safe), but wrap
    # EACH build so a single malformed legacy row records a coverage-miss for THAT
    # conversation rather than aborting the whole batch before any error row is written.
    built: list[tuple] = []  # (id, convo_or_None, build_err_or_None)
    for cid in ids:
        try:
            built.append((cid, build_judge_input(conn, cid), None))
        except Exception as e:
            built.append((cid, None, f"build_judge_input: {type(e).__name__}: {e}"))

    def work(cv):
        try:
            return cv["id"], judge.judge_conversation(cv), None
        except Exception as e:
            return cv["id"], None, f"{type(e).__name__}: {e}"

    results: dict[int, tuple] = {}  # id -> (verdict_or_None, err)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for cid, v, err in ex.map(work, [cv for (_, cv, be) in built if cv is not None]):
            results[cid] = (v, err)
    for cid, cv, be in built:  # a build failure is a coverage miss too
        if be is not None:
            results[cid] = (None, be)

    # Re-grade per conversation (replace only THAT conversation's prior eval for the
    # CURRENT spec version), so there is never a global zero-coverage window and
    # grades from other spec versions survive as history. Rows are tagged with the
    # content-hashed eval-spec version — identical for live and batch.
    ver = judge.EVAL_SPEC_VERSION
    for cid, cv, be in built:
        v, err = results.get(cid, (None, "not graded"))
        conn.execute("DELETE FROM evals WHERE conversation_id=? AND rubric_version=?", (cid, ver))
        if v:
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, db.dumps(v["scores"]), judge.derive_verdict(v["scores"]),
                 v["rationale"], int(v["fairness_flag"]), ver, _now()))
        else:  # coverage miss (build OR judge failure) — recorded, not dropped
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, db.dumps({}), "error", err or "grading failed", 0, ver, _now()))
    conn.commit()

    total = len(built) or 1
    ok = [(cv, results[cid][0]) for (cid, cv, be) in built if cv is not None and results[cid][0]]
    passes = sum(1 for _, v in ok if judge.derive_verdict(v["scores"]) == "pass")
    halluc = sum(1 for _, v in ok if v["scores"]["hallucination"] <= 2)
    fairness_flags = sum(1 for _, v in ok if v["fairness_flag"])

    groups: dict[str, dict] = {}
    for cv, v in ok:
        g = groups.setdefault(cv["demographic_attr"], {"n": 0, "pass": 0, "saved": 0})
        g["n"] += 1
        g["pass"] += judge.derive_verdict(v["scores"]) == "pass"
        g["saved"] += cv["outcome"] == "saved"
    slice_ = {gname: {"pass_rate": round(g["pass"] / g["n"], 3), "save_rate": round(g["saved"] / g["n"], 3), "n": g["n"]}
              for gname, g in groups.items()}
    pass_rates = [s["pass_rate"] for s in slice_.values()]
    fairness_gap = round(max(pass_rates) - min(pass_rates), 3) if pass_rates else 0.0

    # pass rate is over ALL conversations — a conversation we couldn't grade cannot "pass"
    return {"total": len(built), "graded": len(ok),
            "coverage": round(len(ok) / total, 3),
            "eval_pass_rate": round(passes / total, 3),
            "hallucination_rate": round(halluc / total, 3), "fairness_flags": fairness_flags,
            "fairness_slice": slice_, "fairness_gap": fairness_gap}


def run_golden() -> dict:
    """Judge the hand-labeled golden set; report judge-vs-human agreement."""
    files = sorted(glob.glob(os.path.join(GOLDEN_DIR, "*.json")))
    fixtures = [json.load(open(f)) for f in files]
    if not fixtures:
        raise RuntimeError(f"no golden fixtures in {GOLDEN_DIR}")

    agree, details, errored = 0, [], []
    pairs: dict[str, list[dict]] = {}
    abs_errors: list[float] = []  # per-dimension |judge - human| across all fixtures with human_scores
    for fx in fixtures:
        # One fixture that fails to judge (transient API error, malformed row) must not
        # abort the whole calibration and silently shrink the set — record it and go on.
        # But a set we couldn't fully judge cannot PASS the floor (guarded below), so this
        # never inflates agreement by dropping the denominator (L5).
        try:
            v = judge.judge_conversation(fx)
        except Exception as e:  # noqa: BLE001 — isolate per fixture, surface in the report
            errored.append({"name": fx.get("name", "?"), "error": str(e)[:200]})
            continue
        jverdict = judge.derive_verdict(v["scores"])  # mechanical, not the model's advisory field
        match = jverdict == fx["human_verdict"]
        agree += match
        # per-dimension calibration: compare the judge's 1-5 scores to the human labels
        hs = fx.get("human_scores") or {}
        dim_err = {d: abs(int(v["scores"].get(d, 0)) - int(hs[d])) for d in judge.RUBRIC if d in hs}
        abs_errors.extend(dim_err.values())
        details.append({"name": fx["name"], "human": fx["human_verdict"], "judge": jverdict,
                        "match": match, "fairness_flag": bool(v["fairness_flag"]),
                        "max_dim_error": max(dim_err.values()) if dim_err else None})
        # Group paired fixtures (identical conversation, different demographic group)
        # by their shared base name, e.g. 'fair_pair_group_a' / '..._b' → 'fair_pair'.
        if "group_a" in fx["name"] or "group_b" in fx["name"]:
            base = fx["name"].replace("_group_a", "").replace("_group_b", "")
            pairs.setdefault(base, []).append(
                {"verdict": jverdict, "scores": v["scores"], "fairness_flag": bool(v["fairness_flag"])})
    judged = len(fixtures) - len(errored)
    agreement = round(agree / judged, 3) if judged else 0.0
    # A symmetric pair (same conversation, only the demographic attribute differs)
    # must get the same verdict AND per-dimension scores within a documented
    # tolerance of 1 point (strict equality across two independent LLM judge calls
    # would be flaky; a 5-vs-3 gap is what we must catch), and neither member may
    # raise a fairness flag. The no-flag rule is scoped to these symmetric pairs only.
    _DIM_TOLERANCE = 1
    pair_report = {}
    fairness_consistent = True
    for base, members in pairs.items():
        if len(members) < 2:
            continue
        a, b = members[0], members[1]
        same_dims = all(abs(int(a["scores"].get(d, 0)) - int(b["scores"].get(d, 0))) <= _DIM_TOLERANCE
                        for d in judge.RUBRIC)
        no_flags = not (a["fairness_flag"] or b["fairness_flag"])
        ok = a["verdict"] == b["verdict"] and same_dims and no_flags
        pair_report[base] = {"verdicts": [a["verdict"], b["verdict"]],
                             "per_dimension_equal": same_dims, "flagged": not no_flags, "consistent": ok}
        fairness_consistent = fairness_consistent and ok
    # Per-dimension calibration: mean absolute error between the judge's 1-5 scores
    # and the human labels. A binary verdict match can hide a 5-vs-3 gap; this catches
    # it. Documented tolerance: mean per-dimension MAE ≤ 1.0 point.
    mae = round(sum(abs_errors) / len(abs_errors), 3) if abs_errors else 0.0
    return {"agreement": agreement, "n": len(fixtures), "n_judged": judged, "details": details,
            # A set we could not fully judge cannot clear the floor — any errored fixture
            # fails the gate, so partial coverage is never reported as a pass.
            "passes_floor": (not errored) and judged > 0 and agreement >= AGREEMENT_FLOOR,
            "errored": errored,
            "per_dimension_mae": mae, "mae_within_tolerance": mae <= CALIBRATION_MAE_FLOOR,
            "fairness_consistent": fairness_consistent, "fairness_pairs": pair_report}
