"""Theme cards, offer effectiveness, and ranked signals (plan §3d, §8, Phase 4).

Pipeline: de-identified conversation summaries → embed → cluster → per-cluster
LLM summary into a theme card → ranked signals by volume × loss-impact. Plus an
offer-effectiveness aggregation (save rate vs. margin cost per offer type) — the
single most decision-useful view.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

import config
import db
import economics
import llm
from analytics import cluster, embed

_LABEL_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["label", "summary"],
    "additionalProperties": False,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offer_type(offer_made: str | None) -> str:
    if not offer_made:
        return "none"
    if "pause" in offer_made:
        return "pause"
    if "discount" in offer_made:
        return "discount"
    return "other"


def build_conversation_views(conn) -> list[dict]:
    """One de-identified view per conversation. Summary is the customer's own
    (already PII-redacted) words — we cluster on language, not on the label."""
    rows = conn.execute(
        "SELECT c.id, c.transcript_json, c.disposition_json, c.outcome, c.offer_made, "
        "s.price FROM conversations c JOIN subscriptions s ON s.customer_id = c.customer_id"
    ).fetchall()
    views = []
    for r in rows:
        transcript = json.loads(r["transcript_json"])
        disp = json.loads(r["disposition_json"])
        customer_text = " ".join(t["content"] for t in transcript if t["role"] == "user")
        views.append({
            "id": r["id"], "summary": customer_text[:600],
            "churn_reason": disp.get("churn_reason", "unknown"),
            "outcome": r["outcome"], "offer_made": r["offer_made"],
            "offer_type": _offer_type(r["offer_made"]), "price": r["price"],
            "margin_cost": economics.margin_cost(r["offer_made"], r["price"]),
        })
    return views


def _label_cluster(members: list[dict], model: str) -> dict:
    examples = "\n".join(f"- {m['summary'][:160]}" for m in members[:6])
    try:
        return llm.structured(
            model, "Summarize why these customers are cancelling into a short theme. "
            "Return a 2-5 word label and a one-sentence summary.",
            f"Customer statements:\n{examples}", _LABEL_SCHEMA, "theme_label",
            reasoning_effort="minimal", max_output_tokens=300)
    except Exception:
        reason = Counter(m["churn_reason"] for m in members).most_common(1)[0][0]
        return {"label": reason, "summary": f"Customers citing: {reason}."}


def summarize_clusters(views: list[dict], labels: list[int], *, model: str = config.MINI_MODEL) -> list[dict]:
    by_cluster: dict[int, list[dict]] = {}
    for v, lab in zip(views, labels):
        by_cluster.setdefault(lab, []).append(v)
    cards = []
    for members in by_cluster.values():
        size = len(members)
        saved = sum(1 for m in members if m["outcome"] == "saved")
        named = _label_cluster(members, model)
        cards.append({
            "label": named["label"], "summary": named["summary"], "size": size,
            "save_rate": round(saved / size, 3),
            "avg_margin_cost": round(sum(m["margin_cost"] for m in members) / size, 2),
            "example_ids": [m["id"] for m in members[:5]],
        })
    return sorted(cards, key=lambda c: c["size"], reverse=True)


def offer_effectiveness(views: list[dict]) -> list[dict]:
    by_offer: dict[str, list[dict]] = {}
    for v in views:
        by_offer.setdefault(v["offer_type"], []).append(v)
    out = []
    for otype, ms in by_offer.items():
        n = len(ms)
        out.append({"offer": otype, "n": n,
                    "save_rate": round(sum(1 for m in ms if m["outcome"] == "saved") / n, 3),
                    "avg_margin_cost": round(sum(m["margin_cost"] for m in ms) / n, 2)})
    return sorted(out, key=lambda o: o["save_rate"], reverse=True)


def rank_signals(cards: list[dict]) -> list[dict]:
    """Rank themes by volume × loss impact (unsaved conversations)."""
    signals = []
    for c in cards:
        loss = c["size"] * (1 - c["save_rate"])
        rec = (f"'{c['label']}' drives {c['size']} at-risk conversations at a "
               f"{c['save_rate']*100:.0f}% save rate — "
               + ("healthy; hold." if c["save_rate"] >= 0.6
                  else "test a stronger offer play for this theme."))
        signals.append({"label": c["label"], "recommendation": rec, "priority_score": round(loss, 2)})
    return sorted(signals, key=lambda s: s["priority_score"], reverse=True)


def persist(conn, cards: list[dict], signals: list[dict]) -> None:
    conn.execute("DELETE FROM signals")
    conn.execute("DELETE FROM themes")
    for c in cards:
        cur = conn.execute(
            "INSERT INTO themes (label, summary, size, save_rate, avg_margin_cost, example_ids_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (c["label"], c["summary"], c["size"], c["save_rate"], c["avg_margin_cost"],
             db.dumps(c["example_ids"]), _now()))
        tid = cur.lastrowid
        for s in signals:
            if s["label"] == c["label"]:
                conn.execute("INSERT INTO signals (theme_id, recommendation, priority_score, created_at) "
                             "VALUES (?,?,?,?)", (tid, s["recommendation"], s["priority_score"], _now()))
    conn.commit()


def run_analytics(conn, *, model: str = config.MINI_MODEL) -> dict:
    """Full VoC pass: views → embed → cluster → themes → offers → signals → DB."""
    views = build_conversation_views(conn)
    if not views:
        return {"themes": [], "offers": [], "signals": []}
    vectors = embed.embed_summaries([v["summary"] for v in views])
    labels = cluster.cluster(vectors)
    cards = summarize_clusters(views, labels, model=model)
    offers = offer_effectiveness(views)
    signals = rank_signals(cards)
    persist(conn, cards, signals)
    return {"themes": cards, "offers": offers, "signals": signals}
