"""Program-level safety state — the kill switch (plan §4, config floors).

Derived from the DB: once there's a sample, if the eval pass rate or coverage
breaches the configured floors the program enters SAFE MODE, and new live
sessions disclose + route straight to a human instead of running the agent
autonomously. Surfaced in /api/metrics so the dashboard shows the active state.
"""

from __future__ import annotations

import config

_MIN_SAMPLE = 10          # don't judge health until there's a sample
_COVERAGE_FLOOR = 0.90    # evals must cover ≥ this share of conversations


def program_state(conn) -> dict:
    """Return {mode, healthy, reasons, metrics}. healthy=False → safe mode."""
    total = conn.execute("SELECT count(*) FROM conversations").fetchone()[0]
    reasons: list[str] = []
    metrics: dict = {"conversations": total}
    if total >= _MIN_SAMPLE:
        passes = conn.execute("SELECT count(*) FROM evals WHERE verdict='pass'").fetchone()[0]
        graded = conn.execute("SELECT count(*) FROM evals WHERE verdict IN ('pass','fail')").fetchone()[0]
        pass_rate = passes / total
        coverage = graded / total
        metrics.update({"eval_pass_rate": round(pass_rate, 3), "eval_coverage": round(coverage, 3)})
        if pass_rate < config.EVAL_PASS_RATE_FLOOR:
            reasons.append(f"eval pass rate {pass_rate:.0%} below floor {config.EVAL_PASS_RATE_FLOOR:.0%}")
        if coverage < _COVERAGE_FLOOR:
            reasons.append(f"eval coverage {coverage:.0%} below {_COVERAGE_FLOOR:.0%}")
    healthy = not reasons
    return {"mode": "normal" if healthy else "safe", "healthy": healthy,
            "reasons": reasons, "metrics": metrics}
