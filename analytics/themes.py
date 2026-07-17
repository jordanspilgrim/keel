"""Theme cards + ranked signals (plan §3d, §8, Phase 4).

Per cluster, an LLM (MINI_MODEL) summarizes the de-identified members into a
theme card, then signals are ranked by volume x loss-impact:

  theme card = {label, summary, size, save_rate, avg_margin_cost, example_ids}
  signal     = {theme_id, recommendation, priority_score}

Acceptance: produce "top 3 churn drivers" and a comparison like "20%-off saves
~8pp more than pause but costs ~3x the margin" from clustered data only.
"""

from __future__ import annotations

import config


def summarize_clusters(labels: list[int], conversations: list[dict],
                       *, model: str = config.MINI_MODEL) -> list[dict]:
    """Return one theme card per cluster."""
    raise NotImplementedError("themes.summarize_clusters — Phase 4")


def rank_signals(theme_cards: list[dict]) -> list[dict]:
    """Return signals ranked by volume x loss-impact (highest priority first)."""
    raise NotImplementedError("themes.rank_signals — Phase 4")
