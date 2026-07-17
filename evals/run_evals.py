"""Eval runners (plan §3c, §8, Phase 3).

Two entry points:
  grade_all()      -> judge every conversation in the DB, write evals rows,
                      print eval pass rate + hallucination rate + fairness slice.
  run_golden()     -> run the hand-labeled golden set (evals/golden/*.json) on
                      the current agent prompt; fail loudly on regression, and
                      print judge-vs-human agreement (calibration metric).

Golden set is JSON fixtures: {conversation, expected_verdict, expected_scores}.
Breaking the agent prompt must make run_golden() fail (Phase 3 acceptance).
"""

from __future__ import annotations


def grade_all() -> dict:
    raise NotImplementedError("run_evals.grade_all — Phase 3")


def run_golden() -> dict:
    raise NotImplementedError("run_evals.run_golden — Phase 3")
