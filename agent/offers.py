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
- Only ONE offer is active (authorized/presented) at a time; proposing a new one
  supersedes the prior, so "one concrete offer" is enforced, not merely requested.

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


def present(offers: list[Offer], offer: Offer, terms: dict) -> None:
    """Mark an authorized offer as presented with the exact committed terms, and
    supersede any OTHER presented offer — so at most one offer is ever presented
    (the 'one concrete offer' rule, enforced at the point it actually matters)."""
    for o in offers:
        if o is not offer and o.state == "presented":
            o.state = "superseded"
    offer.state, offer.presented_terms = "presented", dict(terms)


def active(offers: list[Offer]) -> Offer | None:
    """The current live offer (latest authorized or presented), or None."""
    for o in reversed(offers):
        if o.state in _LIVE_STATES:
            return o
    return None


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
    """Is a committed offer POSITIVE and within the authorized ceiling for its kind?
    A non-positive committed term (0 or negative) is never valid — the tools only
    authorize genuine positive offers."""
    if kind == "discount":
        pct = float(committed.get("pct", 0))
        return 0 < pct <= float(ceiling.get("pct", 0)) + 0.5
    if kind == "pause":
        months = int(committed.get("months", 0))
        return 0 < months <= int(ceiling.get("months", 0))
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
    accepted, else presented, else rejected. (An offer the customer rejected was
    still made: it matters for cooldown and offer-effectiveness analytics.)"""
    for state in ("accepted", "presented", "rejected"):
        for o in reversed(offers):
            if o.state == state:
                return o
    return None


def offer_summary(offers: list[Offer]) -> str | None:
    """The canonical single offer string for persistence/back-compat: the offer
    extended to the customer (accepted, presented, or rejected), else None."""
    o = extended(offers)
    return human_terms(o) if o is not None else None
