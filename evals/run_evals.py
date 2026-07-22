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


def build_judge_input(conn, cid: int) -> dict:
    """Assemble the judge's evidence for one conversation FROM PERSISTED RECORDS —
    the lossless eval envelope (offer ledger with exact terms, tool facts, policy
    decisions) plus the transcript, disposition, and demographic slice. Both the
    batch runner (grade_all) and the live path (_grade_and_store) call this, so
    they grade on IDENTICAL evidence — no batch/live divergence."""
    r = conn.execute(
        "SELECT c.transcript_json, c.disposition_json, c.outcome, c.evidence_json, cu.demographic_attr "
        "FROM conversations c JOIN customers cu ON cu.id = c.customer_id WHERE c.id=?", (cid,)
    ).fetchone()
    if r is None:
        raise ValueError(f"conversation {cid} not found")
    ev = json.loads(r["evidence_json"] or "{}")
    gev = conn.execute("SELECT type, action FROM guardrail_events WHERE conversation_id=?", (cid,)).fetchall()
    return {"id": cid, "transcript": json.loads(r["transcript_json"]),
            "disposition": json.loads(r["disposition_json"]), "outcome": r["outcome"],
            "demographic_attr": r["demographic_attr"],
            "offers": ev.get("offers", []), "tool_facts": ev.get("tool_facts", []),
            "policy_decisions": ev.get("policy_decisions", []),
            "guardrail_events": [(g["type"], g["action"]) for g in gev]}


def grade_all(conn, *, max_workers: int = 8) -> dict:
    """Judge every conversation from the persisted eval envelope; persist an eval
    row for EACH one — a judge failure records verdict='error' so coverage stays
    honest, never a silent drop. Verdicts are derived mechanically from the scores."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM conversations").fetchall()]
    convos = [build_judge_input(conn, cid) for cid in ids]

    def work(cv):
        try:
            return cv["id"], judge.judge_conversation(cv), None
        except Exception as e:
            return cv["id"], None, f"{type(e).__name__}: {e}"

    results: dict[int, tuple] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for cid, v, err in ex.map(work, convos):
            results[cid] = (v, err)

    # Re-grade per conversation (replace only THAT conversation's prior eval for the
    # CURRENT spec version), so there is never a global zero-coverage window and
    # grades from other spec versions survive as history. Rows are tagged with the
    # content-hashed eval-spec version — identical for live and batch.
    ver = judge.EVAL_SPEC_VERSION
    for cv in convos:
        v, err = results[cv["id"]]
        conn.execute("DELETE FROM evals WHERE conversation_id=? AND rubric_version=?", (cv["id"], ver))
        if v:
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cv["id"], db.dumps(v["scores"]), judge.derive_verdict(v["scores"]),
                 v["rationale"], int(v["fairness_flag"]), ver, _now()))
        else:  # coverage miss — recorded, not dropped
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, rubric_version, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (cv["id"], db.dumps({}), "error", err or "grading failed", 0, ver, _now()))
    conn.commit()

    total = len(convos) or 1
    ok = [(cv, results[cv["id"]][0]) for cv in convos if results[cv["id"]][0]]
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
    return {"total": len(convos), "graded": len(ok),
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

    agree, details = 0, []
    pairs: dict[str, list[dict]] = {}
    abs_errors: list[float] = []  # per-dimension |judge - human| across all fixtures with human_scores
    for fx in fixtures:
        v = judge.judge_conversation(fx)
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
    agreement = round(agree / len(fixtures), 3)
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
    return {"agreement": agreement, "n": len(fixtures), "details": details,
            "passes_floor": agreement >= AGREEMENT_FLOOR,
            "per_dimension_mae": mae, "mae_within_tolerance": mae <= CALIBRATION_MAE_FLOOR,
            "fairness_consistent": fairness_consistent, "fairness_pairs": pair_report}
