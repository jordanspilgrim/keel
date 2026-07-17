"""DB -> dashboard JSON export (plan §6, Phase 5).

Reads keel.db and emits dashboard/data.json in the contract the static
dashboard/index.html expects (the mockup's hard-coded `data` block becomes this
export). Views: KPI row (margin-adjusted save rate hero, save rate, eval pass
rate, guardrail catch rate, compliance coverage), save-rate-vs-margin trend,
top churn drivers, offer-effectiveness scatter, safety/compliance panel.

Run: python -m dashboard.export  ->  writes dashboard/data.json
Then open dashboard/index.html (it fetches data.json).
"""

from __future__ import annotations


def export(out_path: str = "dashboard/data.json") -> dict:
    """Compute all dashboard views from the DB and write JSON. Return the dict."""
    raise NotImplementedError("dashboard.export.export — Phase 5")
