"""Keel — end-to-end flywheel demo (handoff §9 definition of done).

The money demo. Runs the loop once, end to end:

  generate  -> synth.py populates keel.db (seeded)
  converse  -> agent handles each churn scenario under guardrails + disclosure,
               writing conversations + guardrail_events + audit_log
  grade     -> eval harness scores every conversation (evals rows)
  analyze   -> analytics clusters into themes + ranked signals
  act       -> apply ONE signal (add the offer analytics recommends)
  re-measure-> run the next batch and show the lift on margin-adjusted save rate
  export    -> dashboard/data.json, so the dashboard shows the flywheel turning

Phase 5 wires the stages together. Until then, running this prints the phase
status so it's obvious what's built.

Run: python run_demo.py
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError(
        "run_demo — assembled in Phase 5 once Phases 1-4 land. "
        "Phase 0 is runnable now: `python synth.py`, `python economics.py`."
    )


if __name__ == "__main__":
    main()
