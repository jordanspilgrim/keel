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
from agent import guardrails
from analytics import cluster, embed
from evals import judge

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


def _eligibility_join(eligible_only: bool, params: list) -> str:
    """SQL fragment restricting to conversations that PASSED the CURRENT eval spec, so
    Learn is governed by Measure (H1) — an ungraded/failed/error baseline conversation
    cannot influence the intervention. Appends its bind param FIRST (the JOIN's ? precedes
    any WHERE ?), so callers must append run_id/phase params AFTER calling this. Empty when
    not gating (the dashboard and general views still see every conversation)."""
    if not eligible_only:
        return ""
    params.append(judge.EVAL_SPEC_VERSION)
    return " JOIN evals e ON e.conversation_id = c.id AND e.rubric_version = ? AND e.verdict = 'pass' "


def build_conversation_views(conn, *, run_id: str | None = None, phase: str | None = None,
                             eligible_only: bool = False) -> list[dict]:
    """One de-identified view per conversation. Summary is the customer's own
    (already PII-redacted) words — we cluster on language, not on the label.
    Optionally scoped to a run_id/phase and gated on the current-spec eval verdict (H1)."""
    params: list = []
    join = _eligibility_join(eligible_only, params)
    where: list[str] = []
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    sql = (
        "SELECT c.id, c.transcript_json, c.disposition_json, c.outcome, c.offer_made, "
        # the price the customer was actually on at conversation time — immutable; the
        # live subscription price is only a fallback for pre-snapshot historical rows.
        "COALESCE(c.price_at_conversation, s.price) AS price "
        "FROM conversations c JOIN subscriptions s ON s.customer_id = c.customer_id" + join)
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, params).fetchall()
    views = []
    for r in rows:
        transcript = json.loads(r["transcript_json"])
        disp = json.loads(r["disposition_json"])
        # Transcripts are already redacted at persist; redact again here as defense in
        # depth before this text is embedded and sent to the labeler. redact_pii is
        # idempotent, so this is a no-op on well-formed rows and a backstop on any row
        # written by an older build (or a future writer that forgets).
        customer_text = guardrails.redact_pii(
            " ".join(t["content"] for t in transcript if t["role"] == "user"))[0]
        views.append({
            "id": r["id"], "summary": customer_text[:600],
            "churn_reason": disp.get("churn_reason", "unknown"),
            "outcome": r["outcome"], "offer_made": r["offer_made"],
            "offer_type": _offer_type(r["offer_made"]), "price": r["price"],
            # realized cost only when the offer was actually accepted (outcome saved)
            "margin_cost": economics.margin_cost(r["offer_made"], r["price"], accepted=(r["outcome"] == "saved")),
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


def rank_segments(conn, *, run_id: str | None = None, phase: str | None = None,
                  eligible_only: bool = False) -> list[dict]:
    """Rank churn-reason segments by LOSS IMPACT (volume × miss rate) from the
    conversations recorded so far. This is the data-driven 'which segment is
    bleeding' signal — it tells the flywheel WHERE to intervene, instead of the
    target being assumed. Uses the scenario's ground-truth churn_reason (stable),
    not the LLM-derived disposition label. Optionally scoped to a run_id/phase and
    gated on the current-spec eval verdict (H1: Learn is governed by Measure)."""
    params: list = []
    join = _eligibility_join(eligible_only, params)
    where = ["sc.kind='churn'", "sc.churn_reason IS NOT NULL"]
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    rows = conn.execute(
        "SELECT sc.churn_reason AS reason, c.outcome FROM conversations c "
        "JOIN scenarios sc ON sc.id = c.scenario_id" + join +
        " WHERE " + " AND ".join(where), params).fetchall()
    agg: dict[str, list[int]] = {}
    for r in rows:
        a = agg.setdefault(r["reason"], [0, 0])  # [n, saved]
        a[0] += 1
        a[1] += 1 if r["outcome"] == "saved" else 0
    segs = [{"reason": reason, "n": n, "save_rate": round(saved / n, 3),
             "loss": round(n * (1 - saved / n), 2)}
            for reason, (n, saved) in agg.items()]
    return sorted(segs, key=lambda s: (s["loss"], s["n"]), reverse=True)


# The lever the demo can actually pull, and the segment it addresses. A signal
# recommending a segment this lever does not fit is marked lever-incompatible so
# Act refuses it rather than misattributing a lift.
_DISCOUNT_LEVER_SEGMENT = "Price too high"


def _lever_for(reason: str) -> str | None:
    """Which available policy lever addresses a churn segment (None if none fits)."""
    return "discount" if reason == _DISCOUNT_LEVER_SEGMENT else None


def _eval_eligibility(conn, run_id: str | None, phase: str | None) -> dict:
    """Coverage + eligible/excluded counts for the scoped churn conversations, so the
    intervention signal RECORDS how much of the evidence was eval-eligible and the exact
    rule used (H1). This is the provenance that makes 'Measure governs Learn' auditable."""
    where = ["sc.kind='churn'", "sc.churn_reason IS NOT NULL"]
    params: list = []
    if run_id is not None:
        where.append("c.run_id = ?"); params.append(run_id)
    if phase is not None:
        where.append("c.phase = ?"); params.append(phase)
    base = "FROM conversations c JOIN scenarios sc ON sc.id = c.scenario_id WHERE " + " AND ".join(where)
    ver = judge.EVAL_SPEC_VERSION
    total = conn.execute("SELECT count(*) " + base, params).fetchone()[0]
    graded = conn.execute(
        "SELECT count(*) " + base + " AND EXISTS (SELECT 1 FROM evals e WHERE e.conversation_id=c.id "
        "AND e.rubric_version=?)", params + [ver]).fetchone()[0]
    eligible = conn.execute(
        "SELECT count(*) " + base + " AND EXISTS (SELECT 1 FROM evals e WHERE e.conversation_id=c.id "
        "AND e.rubric_version=? AND e.verdict='pass')", params + [ver]).fetchone()[0]
    return {"rule": "current-spec eval verdict = pass", "spec_version": ver,
            "total": total, "graded": graded, "eligible": eligible, "excluded": total - eligible,
            "coverage": round(graded / total, 3) if total else 0.0}


def recommend_intervention(conn, *, run_id: str | None = None, phase: str | None = None,
                           eligible_only: bool = False) -> dict:
    """Build a STRUCTURED intervention signal from the baseline evidence. Analytics
    proposes WHAT to do, not just where: it ranks segments by loss impact and
    selects the highest-loss segment FOR WHICH AN AVAILABLE LEVER EXISTS — acting
    on the worst problem it can actually address, and transparently noting any
    higher-loss segment it has no lever for (rather than either hard-coding the
    target or bailing whenever a lever-less segment happens to rank first).

    When eligible_only is set (the money demo), only conversations that PASSED the current
    eval spec influence the ranking — so a failed/ungraded/hallucinating baseline
    conversation cannot steer the intervention (H1). The eligibility provenance is attached
    to the signal."""
    eligibility = _eval_eligibility(conn, run_id, phase) if eligible_only else None
    segments = rank_segments(conn, run_id=run_id, phase=phase, eligible_only=eligible_only)
    views = build_conversation_views(conn, run_id=run_id, phase=phase, eligible_only=eligible_only)
    offers = offer_effectiveness(views)
    if not segments:
        return {"segment": None, "recommended_lever": None, "lever_compatible": False,
                "confidence": 0.0, "evidence": {}, "offer_effectiveness": offers,
                "unaddressable_higher_loss": [], "eval_eligibility": eligibility}
    # the higher-loss segments we have NO lever for (surfaced, not hidden)
    unaddressable = []
    chosen = None
    for seg in segments:
        if _lever_for(seg["reason"]) is not None:
            chosen = seg
            break
        unaddressable.append({"reason": seg["reason"], "loss": seg["loss"], "save_rate": seg["save_rate"]})
    if chosen is None:
        return {"segment": segments[0]["reason"], "recommended_lever": None, "lever_compatible": False,
                "recommended_action": "no available lever fits any at-risk segment",
                "confidence": 0.0, "evidence": {"segment_ranking": segments[:3]},
                "offer_effectiveness": offers, "unaddressable_higher_loss": unaddressable,
                "eval_eligibility": eligibility}
    lever = _lever_for(chosen["reason"])
    confidence = round(min(1.0, chosen["n"] / 8.0), 2)
    note = (f"enable the {lever} lever for the '{chosen['reason']}' segment"
            + (f" (higher-loss segments {[u['reason'] for u in unaddressable]} have no available lever)"
               if unaddressable else ""))
    return {
        "segment": chosen["reason"],
        "recommended_lever": lever,
        "recommended_action": note,
        "lever_compatible": True,
        "confidence": confidence,
        "evidence": {"n": chosen["n"], "save_rate": chosen["save_rate"], "loss": chosen["loss"],
                     "segment_ranking": segments[:3]},
        "offer_effectiveness": offers,
        "unaddressable_higher_loss": unaddressable,
        "eval_eligibility": eligibility,
    }


def persist_signal(conn, signal: dict, run_id: str | None = None) -> int:
    """Persist the structured intervention signal (into `signals`) and return its
    id, so Act consumes an approved signal ID and the manifest can record it. The
    `run_id` ties the signal to its experiment run (Learn→Act→after lineage)."""
    cur = conn.execute(
        "INSERT INTO signals (theme_id, recommendation, priority_score, run_id, created_at) "
        "VALUES (?,?,?,?,?)",
        (None, db.dumps(signal), float(signal.get("evidence", {}).get("loss", 0.0)), run_id, _now()))
    conn.commit()
    return cur.lastrowid


def load_signal(conn, signal_id: int) -> dict | None:
    """Load a persisted intervention signal by id — Act consumes the signal THROUGH
    the datastore (durable Learn→Act lineage), not an in-memory object."""
    row = conn.execute("SELECT recommendation FROM signals WHERE id=?", (signal_id,)).fetchone()
    return json.loads(row["recommendation"]) if row else None


def rank_signals(cards: list[dict]) -> list[dict]:
    """Rank themes by volume × loss impact (unsaved conversations)."""
    signals = []
    for c in cards:
        loss = c["size"] * (1 - c["save_rate"])
        # Build from the REDACTED label: `persist` redacts c["label"] on the way into
        # `themes`, but this string was built from the raw one six lines earlier and
        # inserted verbatim into `signals`. redact_pii is idempotent, so redacting here
        # keeps the two columns consistent rather than one scrubbed and one not.
        _label = guardrails.redact_pii(c["label"])[0]
        rec = (f"'{_label}' drives {c['size']} at-risk conversations at a "
               f"{c['save_rate']*100:.0f}% save rate — "
               + ("healthy; hold." if c["save_rate"] >= 0.6
                  else "test a stronger offer play for this theme."))
        signals.append({"label": _label, "recommendation": rec, "priority_score": round(loss, 2)})
    return sorted(signals, key=lambda s: s["priority_score"], reverse=True)


def persist(conn, cards: list[dict], signals: list[dict]) -> None:
    # Two kinds of rows live in `signals`: EPHEMERAL dashboard rankings (theme-linked,
    # run_id NULL) that this re-cluster regenerates, and IMMUTABLE experiment signals
    # (run_id set, persisted by persist_signal) that a run's manifest cites by id. Only
    # the ephemeral rows are cleared here — deleting the experiment signals would dangle
    # the manifest's intervention_signal_id after a post-run re-cluster (M2).
    conn.execute("DELETE FROM signals WHERE run_id IS NULL")
    conn.execute("DELETE FROM themes")
    for c in cards:
        cur = conn.execute(
            "INSERT INTO themes (label, summary, size, save_rate, avg_margin_cost, example_ids_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            # label/summary are MODEL-derived text over customer language and land in a
            # durable store — redact them like every other model-derived durable write.
            (guardrails.redact_pii(c["label"])[0], guardrails.redact_pii(c["summary"])[0],
             c["size"], c["save_rate"], c["avg_margin_cost"],
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
