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


def grade_all(conn, *, max_workers: int = 8) -> dict:
    """Judge every conversation; persist evals; return metrics."""
    rows = conn.execute(
        "SELECT c.id, c.transcript_json, c.disposition_json, c.outcome, cu.demographic_attr "
        "FROM conversations c JOIN customers cu ON cu.id = c.customer_id"
    ).fetchall()
    convos = [{"id": r["id"], "transcript": json.loads(r["transcript_json"]),
               "disposition": json.loads(r["disposition_json"]), "outcome": r["outcome"],
               "demographic_attr": r["demographic_attr"]} for r in rows]

    def work(cv):
        try:
            return cv["id"], judge.judge_conversation(cv)
        except Exception:
            return cv["id"], None

    verdicts: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for cid, v in ex.map(work, convos):
            if v:
                verdicts[cid] = v

    conn.execute("DELETE FROM evals")
    for cid, v in verdicts.items():
        conn.execute(
            "INSERT INTO evals (conversation_id, scores_json, verdict, rationale, fairness_flag, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (cid, db.dumps(v["scores"]), v["verdict"], v["rationale"], int(v["fairness_flag"]), _now()),
        )
    conn.commit()

    graded = [(cv, verdicts[cv["id"]]) for cv in convos if cv["id"] in verdicts]
    n = len(graded) or 1
    passes = sum(1 for _, v in graded if v["verdict"] == "pass")
    halluc = sum(1 for _, v in graded if v["scores"]["hallucination"] <= 2)
    fairness_flags = sum(1 for _, v in graded if v["fairness_flag"])

    groups: dict[str, dict] = {}
    for cv, v in graded:
        g = groups.setdefault(cv["demographic_attr"], {"n": 0, "pass": 0, "saved": 0})
        g["n"] += 1
        g["pass"] += v["verdict"] == "pass"
        g["saved"] += cv["outcome"] == "saved"
    slice_ = {gname: {"pass_rate": round(g["pass"] / g["n"], 3), "save_rate": round(g["saved"] / g["n"], 3), "n": g["n"]}
              for gname, g in groups.items()}
    pass_rates = [s["pass_rate"] for s in slice_.values()]
    fairness_gap = round(max(pass_rates) - min(pass_rates), 3) if pass_rates else 0.0

    return {"graded": len(graded), "eval_pass_rate": round(passes / n, 3),
            "hallucination_rate": round(halluc / n, 3), "fairness_flags": fairness_flags,
            "fairness_slice": slice_, "fairness_gap": fairness_gap}


def run_golden() -> dict:
    """Judge the hand-labeled golden set; report judge-vs-human agreement."""
    files = sorted(glob.glob(os.path.join(GOLDEN_DIR, "*.json")))
    fixtures = [json.load(open(f)) for f in files]
    if not fixtures:
        raise RuntimeError(f"no golden fixtures in {GOLDEN_DIR}")

    agree, details = 0, []
    for fx in fixtures:
        v = judge.judge_conversation(fx)
        match = v["verdict"] == fx["human_verdict"]
        agree += match
        details.append({"name": fx["name"], "human": fx["human_verdict"],
                        "judge": v["verdict"], "match": match})
    agreement = round(agree / len(fixtures), 3)
    return {"agreement": agreement, "n": len(fixtures), "details": details,
            "passes_floor": agreement >= AGREEMENT_FLOOR}
