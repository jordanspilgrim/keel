"""resolution-plan §0.1 — the DIRECT test on the CONTROL, bypassing the fairness harness.

Phase 0 repaired the measurement instrument and left the defect live on purpose. Two gates
prove the INSTRUMENT is sound:

    tests/test_guardrails.py::test_the_fairness_gate_checks_orthography_not_just_group
    tests/test_agent_fairness.py::test_the_probe_covers_a_cue_grid_not_a_single_phrasing

They test the GATE. This file tests the CONTROL — `guardrails.redact_pii` called directly, no
harness, no grid — so it cannot be satisfied by the probe set or the oracle changing underneath
it. Different objects; neither substitutes for the other.

DELIBERATELY IN ITS OWN FILE. The primary gate above is primary precisely because this work has
never modified `tests/test_guardrails.py` — it existed at 4df6f13, passed VACUOUSLY over a
pinned probe set, and now fails, so it cannot have been tuned. Adding assertions to that file
would spend that property to save a file. It is worth more than the tidiness.

THE THREE-TIER CUE TAXONOMY these tests encode:

    STRONGEST  "my name is X" / "my name's X" / "name is X" / "i'm called X"
               -> no competing reading exists in English, so the token is a name whatever
                  its case. The uppercase requirement is dropped.
    MEDIUM     "call me X" / "they call me X"
               -> genuinely ambiguous. "call me Bob" and "call me later" are both ordinary
                  sentences and capitalisation is how English marks the difference, so the
                  uppercase requirement is KEPT. Measured on main: `later`, `tomorrow`,
                  `anytime`, `soon`, `now`, `asap`, `tonight` and `whenever` are all absent
                  from _NOT_A_NAME_AFTER_CUE, so the case check is the only thing standing
                  between them and being scrubbed as names.
    WEAK       "it's X" / "X here"
               -> unchanged; already requires two words via _sub_name_min2.

Every assertion below was recorded FAILING against unmodified `agent/guardrails.py` before the
fix existed (resolution-plan §1 rule 3: a test that passes after the fix is worth less than one
that fails before it).
"""

from __future__ import annotations

import pytest

from agent import guardrails

SELF_ID_CUES = ["my name is {n} and I want to cancel.",
                "my name is {n}.",
                "my name's {n}.",
                "i'm called {n}.",
                "call me {n} please.",       # ADDRESS tier — case-blind by owner ruling
                "they call me {n}."]


@pytest.mark.parametrize("name", [
    "emily",      # the 72-cell family: ASCII lowercase, the commonest phrasing
    "josé",       # case COMPOSES with script — lowercase diacritic leaked identically
    "владимир",   # and with non-Latin
    "o'brien",    # and with a joiner
    "ng",         # a 2-character name is its own edge
])
@pytest.mark.parametrize("cue", SELF_ID_CUES)
def test_a_lowercase_name_after_a_self_identification_cue_is_redacted(name, cue):
    """The name must be GONE and the redaction must be REPORTED.

    Both halves matter and they fail differently. A surviving name is plaintext in a durable
    transcript. An unreported redaction writes an all-clear into the telemetry a safety
    reviewer reads — and all 72 baseline leaks in this family reported `types == []`, so the
    transcript carried the name and the log carried the all-clear."""
    out, types = guardrails.redact_pii(cue.format(n=name))
    assert name not in out, f"the name survived: {out!r}"
    assert "name" in types, f"redacted but not reported — a false all-clear: {out!r} {types}"


def test_the_case_that_IS_the_ruling():
    """The leak the ruling closes, in the phrasing that carries it."""
    out, types = guardrails.redact_pii("my name is josé and I want to cancel.")
    assert "josé" not in out and "name" in types, f"the leak is still open: {out!r}"


@pytest.mark.parametrize("phrase", ["later", "tomorrow", "anytime", "soon", "tonight",
                                    "whenever"])
def test_the_ACCEPTED_COST_after_an_address_cue(phrase):
    """AN OWNER DECISION, ASSERTED SO IT CANNOT BE SILENTLY REVERTED.

    "call me later" -> "call me [REDACTED_NAME]" is INTENDED. The uppercase requirement was
    dropped for this cue knowing it scrubs lowercase non-names, because keeping it left a
    lowercase name in plaintext for every customer who does not capitalise — and a privacy
    control whose coverage depends on a shift key protects some people and not others.

    This test exists so that restoring the guard fails loudly rather than looking like a
    tidy-up. If it starts failing, someone has reopened a deliberately closed leak, and that
    needs the owner rather than a code review. `_NOT_A_NAME_AFTER_CUE` cannot mitigate it:
    that list is emotional states and nationalities built for a different cue family, and
    contains none of these words."""
    out, types = guardrails.redact_pii(f"call me {phrase}")
    assert out == f"call me [REDACTED_NAME]", f"the accepted cost was reverted: {out!r}"
    assert "name" in types


@pytest.mark.parametrize("name", ["Bob", "Emily", "Bob Lee", "bob"])
def test_address_cues_redact_a_name_in_either_case(name):
    """The population the guard was already serving must not be lost, and the population it
    was excluding must now be covered."""
    out, types = guardrails.redact_pii(f"call me {name}")
    assert not [t for t in name.split() if t in out], out
    assert "name" in types


@pytest.mark.parametrize("text", [
    "my name is on the account",
    "my name is not the issue",
    "my name is in the system",
    "my name is under my wife's",
    "name is on file",
])
def test_the_capture_does_not_swallow_ordinary_prose(text):
    """Dropping the uppercase test removes the only signal that a name follows the cue, and
    the capture then swallows prose: "my name is on the account" redacted "on". Measured at
    7 of 8 realistic probes damaged, all clean beforehand.

    The bound is a CLOSED-CLASS function-word list consulted only for a LOWERCASE first token,
    so a capitalised token is never tested against it and "my name is An" still redacts. Open-
    class words are not covered and cannot be — see the two documented residuals below."""
    out, types = guardrails.redact_pii(text)
    assert out == text, f"the capture swallowed prose: {out!r}"
    assert "name" not in types


@pytest.mark.parametrize("text", ["my name is spelled wrong", "my name is wrong on the bill"])
def test_the_OPEN_CLASS_over_redaction_residual_is_STILL_OPEN(text):
    """A KNOWN DEFECT, ASSERTED SO IT IS COUNTED RATHER THAN DESCRIBED.

    The closed-class function-word bound closes 5 of the 7 measured prose cases. These two
    survive because `spelled` is a participle and `wrong` an adjective — open-class words, which
    no closed class can cover. Widening the list to reach them means an open-ended vocabulary
    fitted to whichever examples someone happened to try, which is the defect this module has
    already removed twice (a name allowlist, then a months-and-weekdays list).

    So the residual is written down as behaviour instead of buried in a comment. **If this test
    starts failing, the residual has been closed** — that is good news, and the fix is to update
    this test and the note in `_NOT_A_NAME_IN_PROSE`, not to route around it.

    (Not `xfail`: the repo's claims gate parses `failed / passed / skipped` summaries and does
    not model xfails, so an xfail here would put a number in the operating notes that cannot
    reconcile. Encoding it as a passing assertion keeps the count honest without editing another
    thread's gate.)"""
    out, _types = guardrails.redact_pii(text)
    assert out != text and "[REDACTED_NAME]" in out, (
        f"the open-class over-redaction residual is CLOSED ({out!r}) — that is good news. "
        f"Update this test and the note on _NOT_A_NAME_IN_PROSE, which currently states that a "
        f"closed-class list cannot reach these cases.")


@pytest.mark.parametrize("name", ["Emily", "Emily Watson", "JAMAL", "José", "Jean-Pierre"])
def test_the_cases_that_already_worked_still_work(name):
    """ALL-CAPS in particular was measured sound four separate times and is not the defect —
    a fix that widens for it is fixing a non-bug and buys over-redaction for nothing."""
    out, types = guardrails.redact_pii(f"my name is {name} and I want to cancel.")
    assert not [t for t in name.split() if t in out], out
    assert "name" in types


@pytest.mark.parametrize("text", [
    "I am frustrated with the price and want to cancel.",
    "it's Friday and I still have no refund.",
    "I'm Disappointed with the service.",
    "my name is on the account",
])
def test_weak_cues_and_ordinary_prose_are_not_scrubbed(text):
    """The guard is load-bearing after a WEAK cue and the ruling keeps it there. The last case
    is a STRONGEST cue followed by ordinary prose — the run must stop at the first token that
    is not name-shaped, or dropping the case check turns "my name is on the account" into a
    redaction."""
    out, types = guardrails.redact_pii(text)
    assert out == text, f"over-redacted: {out!r}"
    assert "name" not in types
