"""DB → dashboard JSON/JS export (plan §6, Phase 5).

Computes every dashboard view from keel.db and writes ``dashboard/data.js``
(``window.KEEL_DATA = {…}``) plus ``dashboard/data.json``. The dashboard loads
data.js via a <script> tag (works on file://, unlike fetch) and falls back to a
built-in mock if it's absent.

The north-star KPI is the *margin-adjusted* save rate: each save counts as
(1 − margin_given/price), so a save bought with a deep discount counts less than
a clean one. That's the number the org can't game.
"""

from __future__ import annotations

import json
import os

import db
import economics
from agent import disclosure
from analytics import themes as themes_mod
from evals import judge

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def conversation_metrics(conn, *, run_id: str | None = None, phase: str | None = None) -> dict:
    """Save rate and margin-adjusted save rate. Optionally scoped to one experiment
    run/phase (so a flywheel run measures baseline vs after without a DB reset)."""
    where, params = [], []
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT c.outcome, c.offer_made, s.price FROM conversations c "
        "JOIN subscriptions s ON s.customer_id = c.customer_id" + clause, params
    ).fetchall()
    n = len(rows) or 1
    saved = sum(1 for r in rows if r["outcome"] == "saved")
    # margin-adjusted: a save counts as (1 - fraction of price given away)
    madj = 0.0
    for r in rows:
        if r["outcome"] == "saved":
            cost = economics.margin_cost(r["offer_made"], r["price"])
            madj += 1 - (cost / r["price"] if r["price"] else 0)
    return {"n": len(rows), "save_rate": round(saved / n, 4),
            "madj_save_rate": round(madj / n, 4)}


# --- CANONICAL metric definitions (server + dashboard MUST share these) -----
# One source of truth so /api/metrics and the exported dashboard never diverge.
def eval_metrics(conn) -> dict:
    """Eval pass rate over ALL conversations (an ungraded conversation cannot 'pass')
    and coverage = share with a real (non-error) grade. Counts ONLY grades under the
    CURRENT eval spec, one per conversation — so a retained history of superseded
    rubric versions can never push the rate above 1.0."""
    total = conn.execute("SELECT count(*) FROM conversations").fetchone()[0] or 1
    passes, graded = judge.current_spec_eval_counts(conn)
    return {"eval_pass_rate": round(passes / total, 4), "eval_coverage": round(graded / total, 4)}


def compliance_coverage(conn) -> float:
    """Share of conversations whose TRANSCRIPT actually contains the AI disclosure
    — verified against the transcript, not merely the audit-log marker."""
    rows = conn.execute("SELECT transcript_json FROM conversations").fetchall()
    total = len(rows) or 1
    ok = sum(1 for r in rows if disclosure.has_disclosure(json.loads(r["transcript_json"])))
    return round(ok / total, 4)


def build_data(conn, *, before: dict, after: dict, guardrail_counts: dict, catch_rate: float,
               provenance: dict | None = None) -> dict:
    """Assemble the full dashboard data object from the DB + run_demo inputs."""
    views = themes_mod.build_conversation_views(conn)
    total = len(views) or 1

    theme_rows = conn.execute(
        "SELECT label, size, save_rate FROM themes ORDER BY size DESC"
    ).fetchall()
    drivers = [{"label": t["label"], "share": round(t["size"] / total * 100),
                "save_rate": round(t["save_rate"], 3)} for t in theme_rows]

    offers = themes_mod.offer_effectiveness(views)
    pause_cost = next((o["avg_margin_cost"] for o in offers if o["offer"] == "pause"), 0) or 1
    offer_points = [{"label": o["offer"], "save_rate": o["save_rate"],
                     "margin_cost": o["avg_margin_cost"],
                     "rel_cost": round(o["avg_margin_cost"] / pause_cost, 2)}
                    for o in offers if o["offer"] != "none"]

    evalm = eval_metrics(conn)
    compliance = compliance_coverage(conn)
    return {
        "kpis": {
            "madj_save_rate": after["madj_save_rate"],
            "madj_delta_pp": round((after["madj_save_rate"] - before["madj_save_rate"]) * 100, 1),
            "save_rate": after["save_rate"],
            "save_delta_pp": round((after["save_rate"] - before["save_rate"]) * 100, 1),
            "eval_pass_rate": evalm["eval_pass_rate"],
            "eval_coverage": evalm["eval_coverage"],
            "guardrail_catch_rate": round(catch_rate, 4),
            "compliance_coverage": compliance,
        },
        "trend": {"labels": ["Before", "After"],
                  "save": [before["save_rate"], after["save_rate"]],
                  "madj": [before["madj_save_rate"], after["madj_save_rate"]]},
        "drivers": drivers,
        "offers": offer_points,
        "safety": {"catch_rate": round(catch_rate, 4),
                   "compliance": compliance, **guardrail_counts},
        "meta": {"conversations": len(views), "provenance": provenance or {}},
    }


def write_data(data: dict, *, js_path: str | None = None, json_path: str | None = None) -> tuple[str, str]:
    js_path = js_path or os.path.join(OUT_DIR, "data.js")
    json_path = json_path or os.path.join(OUT_DIR, "data.json")
    blob = json.dumps(data, indent=2)
    with open(js_path, "w") as f:
        f.write(f"window.KEEL_DATA = {blob};\n")
    with open(json_path, "w") as f:
        f.write(blob + "\n")
    return js_path, json_path


def export(conn, *, before: dict, after: dict, guardrail_counts: dict, catch_rate: float,
           provenance: dict | None = None) -> dict:
    data = build_data(conn, before=before, after=after, guardrail_counts=guardrail_counts,
                      catch_rate=catch_rate, provenance=provenance)
    write_data(data)
    return data
