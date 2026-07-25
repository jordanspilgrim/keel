"""Offer ledger — the single source of truth for a conversation's retention offer.

The prior design scattered "what was offered" across four disagreeing places:
`offer_made` (a last-write string), an `authorized` dict (the *maximum* terms), the
audit log (`tool:action`, args discarded), and the reply prose (regex-policed).
That made three whole classes of bug possible — the batch judge saw a lossy copy,
economics costed the wrong copy, and the output check had to reconstruct intent
from prose.

This module replaces all of that with ONE typed record per offer, with an explicit
lifecycle:

    authorized → presented → accepted / rejected     (or → superseded)

- `authorized_terms` are what policy actually approved (possibly capped from what
  the agent proposed) — a *ceiling*.
- `presented_terms` are the exact terms the agent committed to the customer (from
  the structured response contract) — always ≤ the ceiling.
- MULTIPLE offers may be AUTHORIZED during a negotiation (the agent may explore a
  discount AND a pause), but exactly ONE is ever PRESENTED — presenting supersedes any
  other presented offer, so "one concrete offer to the customer" is enforced at the
  point it matters, not by invalidating candidates at authorize time.

Outcome, cooldown, disposition, economics, and the eval envelope ALL derive from
the accepted presented offer — never from a stray last authorization.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_LIVE_STATES = ("authorized", "presented")


@dataclass
class Offer:
    id: str
    kind: str                                   # "discount" | "pause"
    authorized_terms: dict                      # ceiling policy approved, e.g. {"pct": 20.0}
    state: str = "authorized"                   # authorized|presented|accepted|rejected|superseded
    presented_terms: dict | None = None         # exact terms committed to the customer (≤ ceiling)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state,
                "authorized_terms": self.authorized_terms, "presented_terms": self.presented_terms}

    @staticmethod
    def from_dict(d: dict) -> "Offer":
        return Offer(id=d["id"], kind=d["kind"], authorized_terms=d.get("authorized_terms") or {},
                     state=d.get("state", "authorized"), presented_terms=d.get("presented_terms"))


def authorize(offers: list[Offer], kind: str, authorized_terms: dict) -> Offer:
    """Record a newly policy-authorized offer. Multiple offers of different kinds
    may be authorized during a negotiation (the agent may explore a discount AND a
    pause); the "one concrete offer" rule is enforced at PRESENT time, not here —
    superseding at authorize time wrongly invalidated an offer the agent was still
    discussing. Returns the new ledger entry."""
    off = Offer(id=f"off_{len(offers) + 1}", kind=kind, authorized_terms=dict(authorized_terms))
    offers.append(off)
    return off


def offer_of_kind(offers: list[Offer], kind: str) -> Offer | None:
    """The latest still-live (authorized or presented) offer of a given kind — what
    a customer-facing commitment of that kind must reconcile against."""
    for o in reversed(offers):
        if o.kind == kind and o.state in _LIVE_STATES:
            return o
    return None


def mark_abandoned(offers: list[Offer]) -> Offer | None:
    """Close a still-PRESENTED offer that the customer never answered.

    Distinct from 'rejected' on purpose. A conversation that hits the turn limit, or one
    where the agent routed a cancellation while an offer sat on the table, is not a
    customer DECLINING the offer — labelling it 'rejected' asserts a customer decision that
    never happened, and that string is rendered verbatim into the judge prompt, where
    `resolution` and `offer_appropriateness` read it. 'abandoned' says what actually
    happened: presented, unanswered."""
    o = presented(offers)
    if o is not None:
        o.state = "abandoned"
    return o


def cheapest_authorized_of_kind(offers: list[Offer], kind: str) -> Offer | None:
    """The LEAST valuable still-live offer of a kind (smallest discount pct / shortest
    pause). Used only by the output-failure fallback: when the agent could not produce a
    valid contract we still honour something it was authorized to give, but we concede the
    minimum rather than whatever happened to be authorized most recently — a failure path
    must never be worth MORE to the customer than the happy path."""
    live = [o for o in offers if o.kind == kind and o.state in _LIVE_STATES]
    if not live:
        return None
    def _value(o: Offer) -> float:
        t = o.authorized_terms or {}
        return float(t.get("pct", t.get("months", 0)) or 0)
    return min(live, key=_value)


def rejected_of_kind(offers: list[Offer], kind: str) -> Offer | None:
    """A REJECTED offer of a kind, if the customer has already declined one — so the
    agent doesn't loop by re-offering the same thing they just turned down."""
    for o in reversed(offers):
        if o.kind == kind and o.state == "rejected":
            return o
    return None


def unpresented_candidates(offers: list[Offer]) -> list[Offer]:
    """Authorized offers of a kind the customer has NOT already SEEN — genuinely new
    levers still on the table. A kind is excluded once ANY offer of that kind has left
    the 'authorized' state (presented / superseded / accepted / rejected): the customer
    has been shown a discount, so a second, redundant authorized discount is not an
    'unseen offer'. (Previously only 'rejected' kinds were excluded, so a redundant
    same-kind authorization was wrongly treated as unseen — blocking a clean close and
    making the fallback present a weaker second offer that corrupted the ledger.) At most
    one per kind (the latest authorized)."""
    already_shown = {o.kind for o in offers if o.state != "authorized"}
    seen: set[str] = set()
    out: list[Offer] = []
    for o in reversed(offers):
        if o.state == "authorized" and o.kind not in already_shown and o.kind not in seen:
            seen.add(o.kind)
            out.append(o)
    return out


def unpresented_new_authorized(offers: list[Offer]) -> Offer | None:
    """True iff a fresh un-declined authorized offer is still on the table (used to
    reject 'abandon to a cancellation while an offer is unpresented'). Returns one such
    offer or None; callers that must CHOOSE among several use `unpresented_candidates`
    and only auto-present when exactly one exists — never by recency."""
    cands = unpresented_candidates(offers)
    return cands[0] if cands else None


def present(offers: list[Offer], offer: Offer, terms: dict) -> None:
    """Mark an authorized offer as presented with the exact committed terms, and
    supersede any OTHER presented offer — so at most one offer is ever presented
    (the 'one concrete offer' rule, enforced at the point it actually matters)."""
    for o in offers:
        if o is not offer and o.state == "presented":
            o.state = "superseded"
    offer.state, offer.presented_terms = "presented", dict(terms)


def presented(offers: list[Offer]) -> Offer | None:
    for o in reversed(offers):
        if o.state == "presented":
            return o
    return None


def accepted(offers: list[Offer]) -> Offer | None:
    for o in reversed(offers):
        if o.state == "accepted":
            return o
    return None


def mark_accepted(offers: list[Offer]) -> Offer | None:
    """The customer accepted the offer on the table — mark the presented offer
    accepted. Returns it, or None if nothing was actually presented (in which case
    there is no save to record)."""
    p = presented(offers)
    if p is not None:
        p.state = "accepted"
    return p


def mark_rejected(offers: list[Offer]) -> Offer | None:
    """The customer rejected the offer on the table — transition the presented offer
    to 'rejected' so the ledger is a faithful state machine (a rejected offer is not
    left dangling as 'presented'). Returns it, or None if none was presented."""
    p = presented(offers)
    if p is not None:
        p.state = "rejected"
    return p


def terms_within(committed: dict, ceiling: dict, kind: str) -> bool:
    """Is a committed offer POSITIVE, a canonical whole unit, and AT OR BELOW the
    authorized ceiling for its kind? Discounts are whole percents and pauses whole
    months; a fractional value (e.g. 20.49% against a 20% ceiling) is rejected outright
    rather than tolerated and later rounded away — so the ledger never holds a term that
    downstream rendering/economics would disagree with."""
    if kind == "discount":
        pct = committed.get("pct")
        if pct is None or not float(pct).is_integer():
            return False
        return 0 < int(pct) <= int(ceiling.get("pct", 0))
    if kind == "pause":
        months = committed.get("months")
        if months is None or not float(months).is_integer():
            return False
        return 0 < int(months) <= int(ceiling.get("months", 0))
    return False


def human_terms(offer: Offer, terms: dict | None = None) -> str:
    """Human-readable label for an offer, preferring the exact presented terms."""
    t = terms or offer.presented_terms or offer.authorized_terms
    if offer.kind == "discount":
        return f"{float(t['pct']):.0f}% discount"
    if offer.kind == "pause":
        return f"{int(t['months'])}-month pause"
    return offer.kind


def extended(offers: list[Offer]) -> Offer | None:
    """The offer that was actually EXTENDED to the customer this conversation —
    accepted, else presented, else rejected, else abandoned. (An offer the customer
    rejected was still made: it matters for cooldown and offer-effectiveness analytics.
    So was one they never answered.)

    `abandoned` is in this tuple because R12 added it as a FOURTH terminal state
    (mark_abandoned, for an offer presented and never answered) and did not add it here.
    Before that, every terminal offer landed on presented/rejected and both were listed,
    so the omission dropped 68 of 160 conversations' offers on the committed run — every
    one of them a LOSS, which made the exclusion purely survivorship-directional: the
    offer-effectiveness panel counted the offers that worked and discarded the ones that
    were ignored. It also meant `offer_made` was NULL for those rows, so the 90-day save
    cooldown never started for 24 customers who had in fact been shown an offer, and the
    judge was handed a self-contradictory envelope ("offer: None" beside an OFFER LEDGER
    line reading "abandoned:pause presented={'months': 3}").

    Any future terminal state MUST be added here too. The states this deliberately
    excludes are `authorized` (policy said yes, the customer never saw it) and
    `superseded` (replaced before it was shown) — neither was extended to anyone."""
    for state in ("accepted", "presented", "rejected", "abandoned"):
        for o in reversed(offers):
            if o.state == state:
                return o
    return None


def offer_summary(offers: list[Offer]) -> str | None:
    """The canonical single offer string for persistence/back-compat: the offer
    extended to the customer (accepted, presented, or rejected), else None."""
    o = extended(offers)
    return human_terms(o) if o is not None else None
