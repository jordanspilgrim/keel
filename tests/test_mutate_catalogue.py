"""The mutation harness's completeness gate — tested by construction, not by a green run.

R17 M22: `mutate.py` derived its completeness expectation from the same file it was checking
(`CLAIMED_CONTROLS` keys were the mutant names), so the check could not fail for the reason it
advertised, and a tree with four safety controls physically deleted printed "every catalogued
control is genuinely verified".

The repaired gate reads `docs/controls.json`. It cannot be verified by running `mutate.py` and
watching it go green — a green run from this harness is precisely the signal M22 established as
untrustworthy. So these tests drive the gap functions directly with synthetic registers, and
assert the three failure directions the old check could not see.

Nothing here runs a mutant or a nested pytest: the gate is deliberately computed before the
baseline, so exercising it costs nothing.
"""

from __future__ import annotations

import json

import pytest

from scripts import mutate


def _register(*entries: dict) -> dict:
    return {"controls": list(entries)}


def _entry(cid: str, file: str = "README.md", anchor: str = "Keel") -> dict:
    return {"id": cid, "claim": f"claim for {cid}",
            "published_in": [{"file": file, "anchor": anchor}]}


def _write(tmp_path, register: dict) -> str:
    path = tmp_path / "controls.json"
    path.write_text(json.dumps(register), encoding="utf-8")
    return str(path)


# --- the real register ------------------------------------------------------------------

def test_the_shipped_register_parses_and_every_claim_is_still_published():
    """The register's provenance pins must resolve, or it has drifted from the documents it
    claims to summarise — which would make it a second hand-maintained literal, i.e. the
    circularity M22 found, moved one file sideways rather than removed."""
    claims = mutate.load_claims()
    assert claims, "the register is empty — the gate would have no expectation"
    _unmutated, _orphans, unpublished = mutate.catalogue_gaps(claims, [m[0] for m in mutate.MUTANTS])
    assert unpublished == [], (
        "a claim in docs/controls.json cites text that is no longer in the named document: "
        f"{unpublished}")


def test_the_register_is_not_derived_from_the_mutant_table():
    """The anti-circularity property itself. If every claim id were simply a mutant name, the
    register would be a restatement of MUTANTS and the check would be a tautology again."""
    claims = mutate.load_claims()
    named = {m[0] for m in mutate.MUTANTS}
    assert set(claims) - named, (
        "every claimed control is also a mutant name — the register has collapsed back onto "
        "the catalogue it is supposed to be independent of")


# --- direction 1: claimed, but no mutant (the check M22 showed was a tautology) ----------

def test_a_claim_with_no_mutant_is_named_and_exits_non_zero(tmp_path, capsys):
    path = _write(tmp_path, _register(_entry("covered"), _entry("claimed_but_unverified")))
    claims = mutate.load_claims(path)

    unmutated, orphans, unpublished = mutate.catalogue_gaps(claims, ["covered"])
    assert unmutated == ["claimed_but_unverified"]
    assert (orphans, unpublished) == ([], [])

    code, lines = mutate.catalogue_report(claims, ["covered"])
    assert code == 2
    assert any("claimed_but_unverified" in ln for ln in lines)
    assert any("CATALOGUE INCOMPLETE" in ln for ln in lines)


def test_main_refuses_to_run_when_a_published_claim_has_no_mutant(tmp_path, capsys, monkeypatch):
    """End to end through main(): a gap must stop the harness BEFORE the baseline, so no
    mutant result can be reported off an incomplete catalogue."""
    path = _write(tmp_path, _register(_entry("claimed_but_unverified")))
    monkeypatch.setattr(mutate, "CLAIMS_PATH", path)
    monkeypatch.setattr(mutate, "MUTANTS", [])          # guarantees the gap, and that no
    monkeypatch.setattr("sys.argv", ["mutate.py"])      # baseline or mutant run is reached

    assert mutate.main() == 2
    out = capsys.readouterr().out
    assert "CATALOGUE INCOMPLETE" in out
    assert "claimed_but_unverified" in out
    assert "baseline" not in out.lower(), "the gate must fire before the baseline runs"


# --- direction 2: a mutant answering to no claim (never checked before) ------------------

def test_an_orphan_mutant_is_reported(tmp_path):
    path = _write(tmp_path, _register(_entry("covered")))
    claims = mutate.load_claims(path)

    unmutated, orphans, _ = mutate.catalogue_gaps(claims, ["covered", "answers_to_nothing"])
    assert orphans == ["answers_to_nothing"]
    assert unmutated == []

    code, lines = mutate.catalogue_report(claims, ["covered", "answers_to_nothing"])
    assert code == 2
    assert any("answers_to_nothing" in ln for ln in lines)
    assert any("ORPHAN MUTANT" in ln for ln in lines)


# --- direction 3: the register drifting from the published documents ---------------------

def test_a_claim_whose_published_anchor_vanished_is_reported(tmp_path):
    path = _write(tmp_path, _register(
        _entry("drifted", file="README.md", anchor="this sentence is not in the README")))
    claims = mutate.load_claims(path)

    _unmutated, _orphans, unpublished = mutate.catalogue_gaps(claims, ["drifted"])
    assert [cid for cid, _f, _a in unpublished] == ["drifted"]

    code, lines = mutate.catalogue_report(claims, ["drifted"])
    assert code == 2
    assert any("STALE CLAIM PROVENANCE" in ln for ln in lines)


def test_a_claim_citing_a_missing_document_is_reported(tmp_path):
    path = _write(tmp_path, _register(_entry("gone", file="docs/no-such-file.md", anchor="x")))
    claims = mutate.load_claims(path)
    _u, _o, unpublished = mutate.catalogue_gaps(claims, ["gone"])
    assert unpublished and "unreadable" in unpublished[0][2]


# --- the register must not be able to degrade into an empty expectation ------------------

@pytest.mark.parametrize("register, missing", [
    ({"controls": [{"id": "a", "claim": "c"}]}, "cites no published claim"),
    ({"controls": [{"id": "a", "published_in": [{"file": "README.md", "anchor": "Keel"}]}]},
     "states no claim"),
    ({"controls": [_entry("dupe"), _entry("dupe")]}, "duplicate control id"),
    # A BLANK OR ABSENT ANCHOR disables the only check that reads a document. `"" in text` is
    # unconditionally True, so the row would pass provenance without being pinned to anything —
    # a hand-maintained literal nothing verifies, which is the M22 shape scoped to one entry.
    ({"controls": [_entry("blank", anchor="")]}, "with no anchor"),
    ({"controls": [_entry("whitespace", anchor="   ")]}, "with no anchor"),
    ({"controls": [{"id": "absent", "claim": "c", "published_in": [{"file": "README.md"}]}]},
     "with no anchor"),
    ({"controls": [_entry("nonstring", anchor=345)]}, "with no anchor"),
    ({"controls": [{"id": "nofile", "claim": "c", "published_in": [{"anchor": "Keel"}]}]},
     "with no file"),
])
def test_a_malformed_register_raises_rather_than_certifying_everything(tmp_path, register, missing):
    """An unreadable or malformed register must not fall back to an empty expectation — that
    would make every catalogue complete by definition, which is the M22 failure with extra
    steps."""
    path = _write(tmp_path, register)
    with pytest.raises(ValueError) as excinfo:
        mutate.load_claims(path)
    assert missing in str(excinfo.value)


def test_a_blank_anchor_would_make_the_provenance_check_vacuous(tmp_path):
    """WHY the load-time validation above is the guard, shown rather than asserted.

    Bypassing load_claims with a blank anchor, catalogue_gaps reports a clean provenance for a
    claim pinned to nothing — because `"" in text` is unconditionally True. Checks 1 and 2 never
    read a document, so that row is then verified by nothing at all."""
    unvalidated = {"c": {"claim": "x", "published_in": [{"file": "README.md", "anchor": ""}]}}
    assert mutate.catalogue_gaps(unvalidated, ["c"]) == ([], [], [])
    assert mutate.catalogue_report(unvalidated, ["c"]) == (0, [])
    # …which is why it can never reach catalogue_gaps through the real loader.
    path = _write(tmp_path, _register(_entry("c", anchor="")))
    with pytest.raises(ValueError):
        mutate.load_claims(path)


def test_main_fail_closes_on_a_register_with_an_unpinned_claim(tmp_path, capsys, monkeypatch):
    """A malformed register must exit 2 (fail closed), not 1 with a traceback out of
    catalogue_gaps. Before the anchor validation, a missing `anchor` key raised KeyError from
    inside catalogue_gaps, which main() does not wrap."""
    monkeypatch.setattr(mutate, "CLAIMS_PATH", _write(tmp_path, _register(_entry("c", anchor=""))))
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    assert mutate.main() == 2
    assert "CANNOT READ THE PUBLISHED-CONTROLS REGISTER" in capsys.readouterr().out


def test_main_refuses_to_run_when_the_register_is_unreadable(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mutate, "CLAIMS_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr("sys.argv", ["mutate.py"])
    assert mutate.main() == 2
    assert "CANNOT READ THE PUBLISHED-CONTROLS REGISTER" in capsys.readouterr().out


# --- the gate must stay ahead of, and independent of, the mutant runner ------------------

def test_a_complete_catalogue_reports_nothing(tmp_path):
    path = _write(tmp_path, _register(_entry("covered")))
    claims = mutate.load_claims(path)
    assert mutate.catalogue_report(claims, ["covered"]) == (0, [])
