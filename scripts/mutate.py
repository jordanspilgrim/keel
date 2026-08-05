"""Mutation harness — turn "mutation-verified" from a claim into a command.

WHY THIS EXISTS
---------------
Across three review passes the single most persistent defect class in this repo was not a bug.
It was: **the commit message says a thing was verified; run the verifier and it wasn't.**
Three separate instances survived into pass 14 — "Tested against a real legacy schema" (true of
half the fix), "the script ASSERTS all three" (two were asserted), "parametrized over EVERY
input" (three inputs were missing). That class is invisible to a green test suite BY
CONSTRUCTION, because a passing suite is exactly what it looks like.

The fix is not another review pass. It is to make the claim executable: state each control and
the test that guards it, then have a script revert the control and check the suite goes red.

USAGE
    .venv/bin/python scripts/mutate.py            # run every mutant
    .venv/bin/python scripts/mutate.py --list     # show the catalogue
    .venv/bin/python scripts/mutate.py -k ceiling # run matching mutants only

Exit code 0 iff every mutant was KILLED. A SURVIVED mutant means the control it names can be
deleted with a green suite — i.e. nothing actually verifies it.

Each entry is (name, file, exact-string-to-replace, replacement, what-it-should-break). The
exact-string form is deliberate: it fails loudly when the code moves, rather than silently
mutating nothing and reporting a false KILL.

SAFETY: mutations are applied to a private copy of the tree under a uniquely-named temp
directory, never to tracked files. Pass 14 observed a shared scratchpad being restored
underneath a running test, producing a false "all passed" — so the copy is verified to contain
the mutation immediately before pytest runs and to be unchanged after.

THE ORACLE, and why it is not just the exit code (pass 15 found this in THIS file).
The first version decided KILLED from `pytest`'s return code alone, with no baseline. That is
wrong in three ways, each of which makes the harness certify a control it never tested:
  * On an ALREADY-RED tree every mutant "kills" and it prints "every catalogued control is
    genuinely verified." Reproduced: one unrelated failing test added, 10/10 KILLED.
  * A collection or import error also exits non-zero, so a mutant that runs ZERO tests
    certifies its control.
  * A mutant can be killed by a test that has nothing to do with the control it names.
So: the baseline is run FIRST and its failing set recorded; a kill requires exit code 1
specifically (test failures, not collection errors) AND at least one failure the baseline did
not already have; and those NEW failing tests are reported so a reader can see the kill is
attributable rather than incidental.

That last part used to be "the baseline must be GREEN", which was a proxy: it delivered
attribution by making "already broken" impossible rather than by checking it. Phase 0 left the
suite deliberately RED on two fairness tests, and the proxy inverted — a mutant that edits a
COMMENT measured as KILLED, citing those two as its evidence. Set comparison states the
property directly; on a green tree the set is empty and nothing changes.

THE COMPLETENESS GATE runs BEFORE the baseline and exits 2 on any gap. Its expectation is
`docs/controls.json` — the register of controls this repo publicly claims — NOT anything in
this file. R17 M22 found the previous in-file expectation was circular and certified a tree
with four safety controls physically deleted; see the comment above `CLAIMS_PATH`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (name, relpath, find, replace, control it should break)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    ("extended_drops_abandoned", "agent/offers.py",
     'for state in ("accepted", "presented", "rejected", "abandoned"):',
     'for state in ("accepted", "presented", "rejected"):',
     "R13 CRITICAL: an unanswered offer vanishes from offer_made, analytics and cooldown"),

    ("ceiling_fallback_upgrades", "agent/runtime.py",
     "    live = offers.presented(rec[\"offers\"])\n    if live is not None and live.kind == kind:",
     "    live = None\n    if live is not None and live.kind == kind:",
     "R13 D8d: a failure path re-presents an already-seen offer at its higher ceiling"),

    ("ssn_cue_drops_is", "agent/guardrails.py",
     r'(?:\s+is)?[:\s#]*)\d{9}\b',
     r'[:\s#]*)\d{9}\b',
     "R13 D8b: 'My SSN is 123456789', the commonest phrasing, leaks"),

    ("unknown_tool_wording", "agent/runtime.py",
     'or "unknown action tool" in _r)',
     'or "unknown tool" in _r)',
     "R13 D8h: unknown tools land on the 'Over-ceiling offers capped' safety tile"),

    ("fulfillment_insert_or_ignore", "agent/runtime.py",
     '        conn.execute(\n            "INSERT INTO offer_fulfillment_requests',
     '        conn.execute(\n            "INSERT OR IGNORE INTO offer_fulfillment_requests',
     "R13 D8f: the durable write becomes a silent no-op on a legacy schema"),

    ("hash_drops_scope_classifier", "agent/guardrails.py",
     "        _SCOPE_INSTRUCTIONS, repr(_SCOPE_SCHEMA),",
     "        config.MINI_MODEL,",
     "R12 D5: gutting the scope prompt leaves the kill switch green at a 0.714 true catch rate"),

    ("hash_drops_pii_replacement", "agent/guardrails.py",
     '        repr(sorted((rx.pattern, rx.flags, _repl_identity(repl), kind)',
     '        repr(sorted((rx.pattern, kind)',
     "R14 H2: a neutered redaction token leaks while still reporting as caught"),

    ("close_dangling_batch", "agent/runtime.py",
     "    _close_dangling_offer(rec, outcome)   # same transition the live path applies (no drift)",
     "    pass  # _close_dangling_offer removed",
     "R12 E2E#14: batch and live persist different ledger evidence for the same terminal"),

    ("resolving_admission_guard", "server.py",
     '        if session.get("_resolving"):\n            # The guard was one-directional',
     '        if False and session.get("_resolving"):\n            # The guard was one-directional',
     "R12 E2E#3: a turn is admitted mid-resolve and races the finalize"),

    ("name_token_ascii_only", "agent/guardrails.py",
     '_NAME_TOK = r"[^\\W\\d_][^\\W\\d_\'’\\-]*(?:[\'’\\-][^\\W\\d_][^\\W\\d_\'’\\-]*)*"',
     '_NAME_TOK = r"[A-Z][a-z]+"',
     "R15: name redaction becomes 100% ASCII / 0% diacritics, apostrophes, non-Latin"),

    # ANCHOR REPAIR, and it is a SPLIT rather than a re-point. Before Phase 1 ONE pattern
    # carried both strong-cue families — `(my name is|name's|name is|i'm called|call me|they
    # call me)` — so one mutant covered both. Phase 1's 4b0f3f5 split it into (a1) DECLARATION
    # and (a1b) ADDRESS, which duplicated the anchored line and left the anchor ambiguous
    # (2 matches -> ANCHOR MISS). Re-pointing at EITHER half alone would silently drop the
    # other from coverage while reporting a clean KILL, so the mutant follows the split.
    ("strong_cue_declaration_inlines_a_broken_token", "agent/guardrails.py",
     '    (re.compile(r"\\b(?i:(my name is|name\'s|name is|i\'m called))"\n'
     '                r"\\s+((?:%s)(?:\\s+(?:%s)){0,2})" % (_NAME_TOK, _NAME_TOK)),',
     '    (re.compile(r"\\b(?i:(my name is|name\'s|name is|i\'m called))"\n'
     '                r"\\s+((?:[^\\\\W\\\\d_]+)(?:\\\\s+(?:[^\\\\W\\\\d_]+)){0,2})"),',
     "R16 CRITICAL: 'my name is William' leaks entirely AND reports types=[]"),

    ("strong_cue_address_inlines_a_broken_token", "agent/guardrails.py",
     '    (re.compile(r"\\b(?i:(call me|they call me))"\n'
     '                r"\\s+((?:%s)(?:\\s+(?:%s)){0,2})" % (_NAME_TOK, _NAME_TOK)),',
     '    (re.compile(r"\\b(?i:(call me|they call me))"\n'
     '                r"\\s+((?:[^\\\\W\\\\d_]+)(?:\\\\s+(?:[^\\\\W\\\\d_]+)){0,2})"),',
     "R16 CRITICAL, address half: 'call me William' leaks entirely AND reports types=[]"),

    ("credentials_keep_the_value", "agent/guardrails.py",
     '        out = _CREDENTIALS.sub(r"\\1[REDACTED_SECRET]", out)',
     '        pass  # credential value left in place',
     "R15: a password/API key/routing number is stored verbatim in the transcript"),

    ("db_rebuild_skipped", "db.py",
     "    _relax_not_null_for_pre_persist_queues(conn)",
     "    pass  # migration skipped",
     "R13 D8f: legacy schemas cannot hold a turn-time pre-write"),

    ("proxy_probe_single_cue", "evals/agent_fairness.py",
     '_PROBE_CUES = [\n    "my name is {n} and I want to cancel.",\n'
     '    "call me {n} please.",\n    "they call me {n}.",\n'
     '    "Hi, this is {n}. I want to cancel.",\n]',
     '_PROBE_CUES = [\n    "Hi, this is {n}. I want to cancel.",\n]',
     "R16 M3: the fairness probe reverts to one cue — R16's CRITICAL lived in a cue it never sent"),

    # ANCHOR RE-POINTED. Phase 0.2 rewrote `_name_survives` from `name.split()[0] in out` to
    # `any(tok in out for tok in name.split())`, so this mutant's anchor stopped matching and
    # the harness reported ANCHOR MISS — the control tested in NEITHER direction, which says
    # less than a survivor does. The fix moved the code out from under the mutant guarding it,
    # and it was invisible for the whole window in which the harness could not run.
    ("proxy_boolean_oracle", "evals/agent_fairness.py",
     '    out, _types = guardrails.redact_pii(cue.format(n=name))\n'
     '    return any(tok in out for tok in name.split())',
     '    out, _types = guardrails.redact_pii(cue.format(n=name))\n'
     '    return not _types',
     "R16 M3: a PARTIAL redaction (name survives, type reported) passes the fairness gate"),

    ("load_signal_assumes_json", "analytics/themes.py",
     '    try:\n        loaded = json.loads(row["recommendation"])\n'
     '    except (json.JSONDecodeError, TypeError):\n        return None\n'
     '    return loaded if isinstance(loaded, dict) else None',
     '    return json.loads(row["recommendation"])',
     "R16 M5: citing an ephemeral signal id raises JSONDecodeError out of a `dict | None` API"),

    # --- the 12 controls the repaired completeness check (R17 M22) surfaced as claimed-but-
    # --- unmutated. Each names a control the repo PUBLICLY claims in docs/controls.json.

    ("eval_pass_rate_floor", "agent/safety.py",
     "        if pass_rate < config.EVAL_PASS_RATE_FLOOR:\n",
     "        if False:  # eval pass-rate floor removed\n",
     "R17 H8: a below-floor eval pass rate no longer forces safe mode"),

    ("eval_coverage_floor", "agent/safety.py",
     "        if coverage < _COVERAGE_FLOOR:\n",
     "        if False:  # eval coverage floor removed\n",
     "R17 H8: below-floor eval coverage no longer forces safe mode"),

    ("guardrail_health_freshness", "agent/safety.py",
     "        if age is not None and age > config.GUARDRAIL_HEALTH_MAX_AGE_DAYS:\n",
     "        if False:  # freshness gate removed\n",
     "R17 H7: a stale guardrail-health result keeps authorizing normal mode"),

    ("discounts_enabled_lever", "agent/policy.py",
     "        if not DISCOUNTS_ENABLED:\n",
     "        if False:  # discount lever ignored\n",
     "R17 M21: the baseline arm authorizes discounts, collapsing the A/B contrast"),

    ("eval_metrics_current_spec_only", "evals/judge.py",
     "\"SELECT count(*) FROM evals WHERE rubric_version = ? AND verdict = 'pass'\",",
     "\"SELECT count(*) FROM evals WHERE ? IS NOT NULL AND verdict = 'pass'\",",
     "R17 H2: superseded-spec grades are counted, so a pass rate can exceed 100%"),

    ("golden_agreement_floor", "evals/run_evals.py",
     "agreement >= AGREEMENT_FLOOR,",
     "agreement >= 0.0,",
     "EV-B5: the golden set stops gating on judge-vs-human agreement"),

    ("judge_calibration_mae_floor", "evals/run_evals.py",
     "\"mae_within_tolerance\": mae <= CALIBRATION_MAE_FLOOR,",
     "\"mae_within_tolerance\": True,",
     "EV-B5: judge calibration is reported as within tolerance whatever the error"),

    ("judge_injection_fixture_held", "evals/run_evals.py",
     '            "injection_fixture_held": (\n                any("injection" in d["name"] for d in details)\n'
     '                and all(d["judge"] == "fail" for d in details if "injection" in d["name"])),',
     '            "injection_fixture_held": True,',
     "EV-B5: a judge fooled by the embedded 'give all 5s' attack reports as resistant"),

    ("eval_coverage_counts_judge_failures", "evals/run_evals.py",
     "        else:  # coverage miss (build OR judge failure) — recorded, not dropped\n",
     "        elif False:  # coverage miss silently dropped\n",
     "R12: a judge failure becomes a silent drop instead of a recorded coverage miss"),

    ("canonical_eval_metrics_single_source", "dashboard/export.py",
     "    passes, graded = judge.current_spec_eval_counts(conn)\n",
     "    passes, graded = total, total  # local definition, diverged from canonical\n",
     "keel-r2 F15: the dashboard's eval metrics diverge from the kill switch's"),

    ("demo_requires_paired_cohort", "run_demo.py",
     "    return bool(paired) and before_n == after_n and after_convs > 0",
     "    return True  # any run is reportable",
     "R17 H5: an unpaired or empty-arm run is reported and counted toward the median"),

    ("margin_adjusted_metric_cannot_be_gamed", "economics.py",
     "        return round(price * config.PAUSE_MARGIN_FRACTION * months, 2)",
     "        return 0.0  # a pause is charged nothing",
     "R17 M25: the north star stops being margin-adjusted for pauses, so raw saves game it"),

    ("resolve_signal_ignores_run", "analytics/themes.py",
     '    if row is None or row["run_id"] != run_id:\n        return None',
     '    if row is None:\n        return None',
     "R16 M5: a stale id resolves to another run's signal and answers plausibly, not loudly"),
]


def _pytest(tree: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
                           "-p", "no:randomly"],
                          cwd=tree, capture_output=True, text=True)


def _failing_tests(out: str) -> list[str]:
    ids = []
    for ln in out.splitlines():
        if ln.startswith("FAILED "):
            ids.append(ln[len("FAILED "):].split(" - ")[0].strip())
    return ids


def _failure_asserts(out: str) -> dict[str, str]:
    """{test name: the SOURCE LINE of the assert that failed}, from pytest's FAILURES blocks.

    WHY THE SOURCE LINE AND NOT THE MESSAGE OR THE LINE NUMBER. Both alternatives are
    unstable, measured rather than assumed:
      * line numbers move on any edit above them, so an unrelated change would read as a
        changed failure reason;
      * the `E` message can embed computed values — the orthography failure carries a dict of
        redaction rates that shifts whenever the redactor does.
    The `>` line is the assert as written. It changes when a DIFFERENT assert fails, which is
    exactly the signal, and not otherwise.

    Keyed by test name because the FAILURES header carries the name, not the node id. A name
    appearing twice among the failures is dropped rather than guessed at: an unattributable
    signature must never manufacture a kill.
    """
    sigs: dict[str, str] = {}
    seen: set[str] = set()
    current = None
    for ln in out.splitlines():
        header = re.match(r"^_{3,}\s+(\S.*?)\s+_{3,}$", ln)
        if header:
            current = header.group(1)
            if current in seen:
                sigs.pop(current, None)          # ambiguous name — refuse to attribute
                current = None
            seen.add(header.group(1))
            continue
        if current and current not in sigs and ln.startswith("> "):
            sigs[current] = ln[1:].strip()
    return sigs


def _changed_reason(failing: set[str], base_failing: set[str],
                    asserts: dict[str, str], base_asserts: dict[str, str]) -> set[str]:
    """Tests failing in BOTH runs whose failing assert is not the same one.

    THE HOLE THIS CLOSES. Comparing node ids alone, a mutant whose only guarding test is
    already failing at baseline reports SURVIVED whether or not the control is guarded — the
    id is unchanged and a completely different failure reason is invisible. Measured live:
    `proxy_probe_single_cue` on the pre-Phase-1 tree failed at
    `assert v["leaked_cells"] == 0` at baseline and at `assert len(af._PROBE_CUES) >= 4`
    under mutation. Same node id, guard firing, reported SURVIVED.

    A false SURVIVED is the safe direction — it understates coverage and can never certify a
    control — but it is still a false report, and it reads as "no test kills this" when the
    truth is "its test is busy failing at something else".
    """
    changed = set()
    for node in failing & base_failing:
        name = node.split("::")[-1]
        before, after = base_asserts.get(name), asserts.get(name)
        if before is not None and after is not None and before != after:
            changed.add(node)
    return changed


def _baseline() -> tuple[bool, set[str], dict[str, str], str]:
    """Record the unmutated tree's FAILING SET, and whether it is usable as a reference.

    THIS USED TO REQUIRE A GREEN BASELINE, and that was a proxy standing in for the property.
    The property the harness actually needs is ATTRIBUTION — "a mutant cannot be shown to break
    something that is already broken" (docstring above). `returncode == 0` expressed that only
    by making "already broken" impossible, which is a different and weaker thing, and it fails
    the moment a suite is deliberately red.

    It failed here. Phase 0 repaired three verifiers and left `pytest` RED on two fairness tests
    ON PURPOSE — a repaired gate has to be seen failing before the defect under it is fixed. At
    that point the old oracle, `returncode == 1 and bool(failing)`, is satisfied by the
    PRE-EXISTING failures for every mutant unconditionally. MEASURED: a mutant that edits a
    COMMENT — zero behavioural change — reported KILLED, citing those two fairness tests as its
    evidence. The green-baseline abort was the only thing standing between this tree and every
    catalogued control certifying falsely, which is the pass-15 failure recorded in the
    docstring above, re-created by two decisions that were each individually correct.

    So the baseline now records WHICH tests fail, and a kill requires a failure that is NOT in
    that set. On a green tree the set is empty and the behaviour is identical to before; on a
    red tree the harness still works; and in both cases every kill is attributable BY
    CONSTRUCTION rather than by assuming a clean baseline made it so.

    Returns (usable, failing_set, failing_asserts, detail). Not usable if pytest could not run
    (a collection error tells us nothing about any control), or if the failing set is UNSTABLE
    across two runs — a flaky test would otherwise masquerade as a kill for whichever mutant it
    happened to fire under.
    """
    tmp = tempfile.mkdtemp(prefix="keel-mut-baseline-")
    tree = os.path.join(tmp, "tree")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
    runs = [_pytest(tree) for _ in range(2)]
    shutil.rmtree(tmp, ignore_errors=True)
    sets = [set(_failing_tests(p.stdout)) for p in runs]
    tail = [ln for ln in runs[0].stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    detail = tail[0].strip()

    for proc in runs:
        if proc.returncode not in (0, 1):
            return False, set(), {}, (f"{detail}  [pytest exit {proc.returncode} — collection/usage "
                                      f"error, the suite did not run]")
    if sets[0] != sets[1]:
        drift = sorted(sets[0] ^ sets[1])
        return False, set(), {}, (f"{detail}  [UNSTABLE across two runs — {len(drift)} test(s) "
                                  f"differ: {', '.join(t.split('::')[-1] for t in drift[:3])}]")
    a0, a1 = (_failure_asserts(p.stdout) for p in runs)
    if a0 != a1:
        return False, set(), {}, (f"{detail}  [UNSTABLE failing asserts across two runs — the "
                                  f"changed-reason check would be non-deterministic]")
    return True, sets[0], a0, detail


# THE COMPLETENESS GATE, and why its expectation lives OUTSIDE this file (R17 M22).
#
# Until M22 the expectation was a `CLAIMED_CONTROLS` dict right here, whose keys WERE the mutant
# names, hand-edited a dozen lines from `MUTANTS` in the same commit. Its docstring promised
# "the catalogue cannot silently fall behind again". What it computed was `{keys} - {names}`
# over two literals maintained together, so it could only catch one clerical slip — adding to
# the dict and forgetting the table — and could NOT catch the failure it advertised: a control
# that ships in code, is claimed in the README, and is absent from BOTH structures is invisible
# to a check derived from those structures. An expectation derived from the artifact it checks
# cannot fail for the reason it claims to.
#
# M22 REPRODUCED the consequence: a tree with the eval pass-rate floor, the eval coverage floor,
# the guardrail-health freshness gate and the DISCOUNTS_ENABLED reject block all physically
# deleted ran a green baseline, killed all 17 mutants, and printed "all mutants killed — every
# catalogued control is genuinely verified".
#
# So the expectation now comes from `docs/controls.json`: a register of the controls this repo
# PUBLICLY CLAIMS, each entry naming a document that makes the claim and a literal string from
# it. Three checks, two of them new:
#
#   1. every claimed control has a mutant   — the check M22 showed was a tautology
#   2. every mutant answers to a claim      — the reverse direction, never checked before
#   3. every claim's anchor still resolves  — so the register cannot drift from the documents
#      it summarises. The same exact-string discipline the mutant table already applies to
#      source code, applied to claims: fail loudly when the text moves, rather than quietly
#      checking nothing.
#
# HONEST LIMIT, stated rather than papered over. This makes the check NON-CIRCULAR. It does not
# make it COMPLETE, and no version of it can: a control implemented in code and claimed NOWHERE
# is undetectable by any claims-based check, because finding it would mean deriving intent from
# implementation — the circularity being removed. "Every control we claim has a mutant" is
# enforceable; "every control the code has, has a mutant" is not. The residual mitigation is
# process, not code: a control lands with its claim and its mutant in the same change.
CLAIMS_PATH = os.path.join(ROOT, "docs", "controls.json")


def load_claims(path: str = "") -> dict[str, dict]:
    """Read the published-controls register. Raises rather than degrading to an empty
    expectation — a register that cannot be read must not silently certify everything."""
    with open(path or CLAIMS_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    claims: dict[str, dict] = {}
    for entry in doc["controls"]:
        cid = entry["id"]
        if cid in claims:
            raise ValueError(f"duplicate control id in the register: {cid!r}")
        if not entry.get("claim"):
            raise ValueError(f"control {cid!r} states no claim")
        if not entry.get("published_in"):
            raise ValueError(f"control {cid!r} cites no published claim")
        for src in entry["published_in"]:
            file, anchor = src.get("file"), src.get("anchor")
            if not isinstance(file, str) or not file.strip():
                raise ValueError(f"control {cid!r} cites a published claim with no file")
            # A BLANK ANCHOR IS THE VACUITY HOLE, and it must fail at load rather than at use.
            # `"" in text` is unconditionally True, so a blank anchor makes check 3 pass for
            # that row without reading anything — and check 3 is the ONLY check that touches a
            # document. Checks 1 and 2 compare the register against MUTANTS, so with its anchor
            # disabled a row is a free-floating hand-maintained literal that nothing verifies:
            # the M22 shape again, scoped to one entry. It is also exactly what someone types
            # when they cannot find a crisp string to pin to, and it stays green forever.
            if not isinstance(anchor, str) or not anchor.strip():
                raise ValueError(f"control {cid!r} cites {file} with no anchor — a blank "
                                 f"anchor makes the provenance check vacuous for that claim")
        claims[cid] = entry
    return claims


def catalogue_gaps(claims: dict[str, dict], mutant_names, root: str = "") -> tuple[
        list[str], list[str], list[tuple[str, str, str]]]:
    """(claimed-but-unmutated, mutated-but-unclaimed, claims whose published anchor is gone)."""
    root = root or ROOT
    names = set(mutant_names)
    unmutated = sorted(set(claims) - names)
    orphans = sorted(names - set(claims))
    unpublished: list[tuple[str, str, str]] = []
    for cid in sorted(claims):
        for src in claims[cid]["published_in"]:
            try:
                with open(os.path.join(root, src["file"]), encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                unpublished.append((cid, src["file"], f"unreadable ({exc.strerror})"))
                continue
            if src["anchor"] not in text:
                unpublished.append((cid, src["file"], src["anchor"]))
    return unmutated, orphans, unpublished


def catalogue_report(claims: dict[str, dict], mutant_names,
                     root: str = "") -> tuple[int, list[str]]:
    """Exit code + lines to print. 0 iff the catalogue answers every published claim."""
    unmutated, orphans, unpublished = catalogue_gaps(claims, mutant_names, root)
    out: list[str] = []
    if unmutated:
        out.append("CATALOGUE INCOMPLETE — these controls are PUBLICLY CLAIMED but have no "
                   "mutant, so nothing verifies them:")
        for cid in unmutated:
            where = ", ".join(s["file"] for s in claims[cid]["published_in"])
            out.append(f"    {cid}: {claims[cid]['claim']}   [claimed in {where}]")
    if orphans:
        out.append("ORPHAN MUTANT(S) — these answer to no claim in "
                   f"{os.path.relpath(CLAIMS_PATH, ROOT)}, so killing them certifies nothing "
                   "this repo actually promised:")
        for name in orphans:
            out.append(f"    {name}")
    if unpublished:
        out.append("STALE CLAIM PROVENANCE — the register cites text that is no longer in the "
                   "document, so it has drifted from what the repo publishes:")
        for cid, path, anchor in unpublished:
            out.append(f"    {cid}: {path} no longer contains {anchor!r}")
    if out:
        out.append("")
        out.append("Do NOT close this by deleting register entries or by relaxing this check — "
                   "the register is the independent statement of intent, so a gap here IS the "
                   "finding. Add the missing mutant, or correct the published claim.")
        return 2, out
    return 0, out


def _run(mut, keep: bool, baseline_failing: set[str] = frozenset(),
         baseline_asserts: dict[str, str] | None = None) -> tuple[str, bool, str]:
    name, rel, find, repl, control = mut
    tmp = tempfile.mkdtemp(prefix=f"keel-mut-{name}-")
    tree = os.path.join(tmp, "tree")
    shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", "*.pyc", "keel.db*", ".pytest_cache"))
    path = os.path.join(tree, rel)
    src = open(path).read()
    if src.count(find) != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        return name, False, f"ANCHOR MISS ({src.count(find)} matches) — the code moved; update the catalogue"
    open(path, "w").write(src.replace(find, repl))
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()

    proc = _pytest(tree)
    # the mutation must still be present — guards against a concurrent restore (see docstring)
    if hashlib.sha256(open(path, "rb").read()).hexdigest() != digest:
        shutil.rmtree(tmp, ignore_errors=True)
        return name, False, "TREE MUTATED UNDER THE RUN — result discarded"
    failing = set(_failing_tests(proc.stdout))
    # ATTRIBUTION: only failures the BASELINE did not already have can be credited to this
    # mutation. On a green tree baseline_failing is empty and this reduces exactly to the old
    # `bool(failing)`; on a red tree it is what stops the pre-existing failures certifying
    # every mutant. See _baseline() for the measurement that forced this.
    new_failures = failing - baseline_failing
    # ...and tests that were ALREADY failing but are now failing at a DIFFERENT assert. Without
    # this, a control whose only guard is already red reports SURVIVED however well it is guarded.
    changed = _changed_reason(failing, baseline_failing, _failure_asserts(proc.stdout),
                              baseline_asserts or {})
    # Exit code 1 means TEST FAILURES. 2 is a collection/usage error, 3 internal, 4 usage,
    # 5 no tests collected — a mutant that breaks the import runs zero tests and must not be
    # credited with a kill.
    killed = proc.returncode == 1 and bool(new_failures or changed)
    repaired = baseline_failing - failing
    if proc.returncode not in (0, 1):
        detail = f"NOT A KILL — pytest exit {proc.returncode} (collection/import error, zero tests ran)"
    elif killed and new_failures:
        shown = ", ".join(t.split("::")[-1] for t in sorted(new_failures)[:3])
        detail = (f"{len(new_failures)} NEW test failure(s): {shown}"
                  f"{' …' if len(new_failures) > 3 else ''}")
        if changed:
            detail += f"  (+{len(changed)} already-failing test(s) now failing at a new assert)"
    elif killed:
        shown = ", ".join(t.split("::")[-1] for t in sorted(changed)[:2])
        detail = (f"{len(changed)} already-failing test(s) now fail at a DIFFERENT assert: {shown}"
                  f" — the guard fired inside a test the baseline had already reddened")
    else:
        tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
        detail = tail[0].strip()
        if failing:
            detail += (f"  [all {len(failing)} failure(s) were already failing at baseline, at the "
                       f"same assert]")
    if repaired:
        # A mutation that makes a baseline failure PASS is not a kill and is worth surfacing:
        # it means the mutated line is implicated in that failure.
        detail += (f"  [NB: this mutation REPAIRED {len(repaired)} baseline failure(s): "
                   f"{', '.join(t.split('::')[-1] for t in sorted(repaired)[:2])}]")
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return name, killed, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-k", default="", help="substring filter on mutant name")
    ap.add_argument("--keep", action="store_true", help="keep temp trees for inspection")
    args = ap.parse_args()

    chosen = [m for m in MUTANTS if args.k in m[0]]
    if args.list:
        for name, rel, _f, _r, control in chosen:
            print(f"  {name:32} {rel:22} {control}")
        return 0

    try:
        claims = load_claims()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"CANNOT READ THE PUBLISHED-CONTROLS REGISTER ({CLAIMS_PATH}): {exc}")
        print("The completeness gate has no independent expectation to check against, so a "
              "green run below would mean nothing. Fix the register.")
        return 2
    code, lines = catalogue_report(claims, [m[0] for m in MUTANTS])
    for line in lines:
        print(line)
    if code:
        return code

    usable, base_failing, base_asserts, base_detail = _baseline()
    print(f"baseline (unmutated): {base_detail}")
    if not usable:
        print("\nABORTING — the baseline is not usable as a reference, so nothing below could be "
              "attributed to a mutation. This is NOT the same as the suite being red: a red "
              "baseline is fine and is handled by set comparison. Fix the collection error or "
              "the flake first.")
        return 2
    if base_failing:
        print(f"baseline has {len(base_failing)} PRE-EXISTING failure(s); a kill requires a "
              f"failure NOT in this set:")
        for t in sorted(base_failing):
            print(f"    {t}")
    print(f"running {len(chosen)} mutant(s) — each must be KILLED (a NEW test goes red)\n")
    survived, stale = [], []
    for mut in chosen:
        name, killed, detail = _run(mut, args.keep, base_failing, base_asserts)
        tag = "KILLED  " if killed else ("ANCHOR  " if detail.startswith("ANCHOR") else "SURVIVED")
        print(f"  {tag}  {name:32} {detail}")
        if detail.startswith("ANCHOR"):
            stale.append((name, detail))
        elif not killed:
            survived.append((name, mut[4]))
    print()
    if stale:
        # NOT a coverage claim either way — the catalogue is out of date and says nothing
        # about the control. Reporting it under "can be deleted" was itself misleading.
        print(f"{len(stale)} STALE ANCHOR(S) — the catalogue no longer matches the code, so these "
              f"controls were NOT tested in either direction:")
        for name, detail in stale:
            print(f"    {name}: {detail}")
    if survived:
        print(f"{len(survived)} SURVIVED — these controls can be deleted with a green suite:")
        for name, control in survived:
            print(f"    {name}: {control}")
    if survived or stale:
        return 1
    if base_failing:
        print(f"all mutants killed — every catalogued control is genuinely verified "
              f"(each attributed against a baseline that already had {len(base_failing)} "
              f"failure(s): a NEW test failing, or an already-failing one failing at a "
              f"DIFFERENT assert)")
    else:
        print("all mutants killed — every catalogued control is genuinely verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
