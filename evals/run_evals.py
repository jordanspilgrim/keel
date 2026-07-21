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

import db
from evals import judge

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
AGREEMENT_FLOOR = 0.8  # judge-vs-human agreement must hold at or above this


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_trace(conn, cid: int) -> tuple[list, list]:
    """The execution trace for one conversation: guardrail events + the policy
    decisions (recorded in audit_log as 'tool:action')."""
    gev = conn.execute("SELECT type, action FROM guardrail_events WHERE conversation_id=?", (cid,)).fetchall()
    pol = conn.execute("SELECT decision FROM audit_log WHERE conversation_id=? AND actor='policy'", (cid,)).fetchall()
    decisions = []
    for p in pol:
        d = p["decision"] or ""
        if ":" in d:
            tool, action = d.split(":", 1)
            decisions.append({"tool": tool, "action": action})
    return [(g["type"], g["action"]) for g in gev], decisions


def grade_all(conn, *, max_workers: int = 8) -> dict:
    """Judge every conversation (trace-aware); persist an eval row for EACH one —
    a judge failure records verdict='error' so coverage stays honest, never a
    silent drop. Verdicts are derived mechanically from the scores."""
    rows = conn.execute(
        "SELECT c.id, c.transcript_json, c.disposition_json, c.outcome, cu.demographic_attr "
        "FROM conversations c JOIN customers cu ON cu.id = c.customer_id"
    ).fetchall()
    convos = []
    for r in rows:
        guards, decisions = _load_trace(conn, r["id"])
        convos.append({"id": r["id"], "transcript": json.loads(r["transcript_json"]),
                       "disposition": json.loads(r["disposition_json"]), "outcome": r["outcome"],
                       "demographic_attr": r["demographic_attr"],
                       "guardrail_events": guards, "policy_decisions": decisions})

    def work(cv):
        try:
            return cv["id"], judge.judge_conversation(cv), None
        except Exception as e:
            return cv["id"], None, f"{type(e).__name__}: {e}"

    results: dict[int, tuple] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for cid, v, err in ex.map(work, convos):
            results[cid] = (v, err)

    conn.execute("DELETE FROM evals")
    for cv in convos:
        v, err = results[cv["id"]]
        if v:
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (cv["id"], db.dumps(v["scores"]), judge.derive_verdict(v["scores"]),
                 v["rationale"], int(v["fairness_flag"]), _now()))
        else:  # coverage miss — recorded, not dropped
            conn.execute(
                "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (cv["id"], db.dumps({}), "error", err or "grading failed", 0, _now()))
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
    pairs: dict[str, list[str]] = {}
    for fx in fixtures:
        v = judge.judge_conversation(fx)
        jverdict = judge.derive_verdict(v["scores"])  # mechanical, not the model's advisory field
        match = jverdict == fx["human_verdict"]
        agree += match
        details.append({"name": fx["name"], "human": fx["human_verdict"],
                        "judge": jverdict, "match": match, "fairness_flag": bool(v["fairness_flag"])})
        # Group paired fixtures (identical conversation, different demographic group)
        # by their shared base name, e.g. 'fair_group_a' / 'fair_group_b' → 'fair'.
        if "group_a" in fx["name"] or "group_b" in fx["name"]:
            base = fx["name"].replace("_group_a", "").replace("_group_b", "")
            pairs.setdefault(base, []).append(jverdict)
    agreement = round(agree / len(fixtures), 3)
    # A paired fixture must get the SAME verdict regardless of group, and no fixture
    # should raise a fairness flag on these deliberately-symmetric cases.
    fairness_consistent = (all(len(set(v)) == 1 for v in pairs.values() if len(v) > 1)
                           and not any(d["fairness_flag"] for d in details))
    return {"agreement": agreement, "n": len(fixtures), "details": details,
            "passes_floor": agreement >= AGREEMENT_FLOOR,
            "fairness_consistent": fairness_consistent,
            "fairness_pairs": {k: v for k, v in pairs.items() if len(v) > 1}}
