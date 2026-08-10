# ADR-0027: A hash is not evidence

- **Status**: Accepted
- **Date**: 2026-08-10
- **Context**: [ADR-0022](0022-verity-eval-harness.md) introduced `verity eval`
  and stated that it closed invariant 7 ("no published metric without a
  retained, re-derivable artifact"). A verification pass over that work —
  run against the repository rather than read from its docstrings — found it
  had not. This ADR records six specific failures, each independently
  checkable, and the repair.

## What the audit found

1. **A content hash re-derives nothing.** `TrialRecord.artifact_hash` was
   `sha256(tree)[:16]`. That identifies a directory; it cannot reconstruct
   one. Once the directory is gone — precisely what happened to three pilots
   on 2026-08-10 — the hash establishes nothing a third party could
   re-check, which is the whole of what invariant 7 asks for.
2. **The default output path was git-ignored.** `verity eval --work-root`
   defaulted to `.verity/eval`, and `.gitignore` excludes `.verity/`. The
   harness built to satisfy invariant 7 wrote its artifact, by default,
   where git would never track it.
3. **`experiments/*/trials/` remained ignored.** The exact structural cause
   of the original loss was unchanged. Phase 0 had added a guard against the
   *accidental* `rm -rf` but not against the artifact being untracked in the
   first place.
4. **Nothing persisted a `TrialRecord`.** Neither `bench/trial.py` nor
   `bench/eval.py` wrote to disk at all. `created_at` and `transcript_path`
   were computed and discarded; the aggregate report survived only if the
   operator remembered `--json`.
5. **No `TrialSpec` was committed anywhere.** ADR-0022's own verification
   section claims `verity eval` "reproduced pilot 8's result exactly." The
   spec that did it was never committed, so the claim about the reproduction
   was itself not re-derivable.
6. **The CLI could not express the metric ADR-0022 claimed to reproduce.**
   `run_eval` was called without a `metric_fn`, so metrics defaulted to
   `{"success": exit_code == 0}`. Pilot 8's headline metric is `tie_correct`.
   A spec asking for it got `insufficient_data` — printed inside a report
   that otherwise looked publishable.

Findings 2 and 3 are the ones worth dwelling on: the mechanism that
destroyed the original evidence was still in place, in the same file, under
the tool built to prevent it.

## Decision

**The artifact is a diff against a hash-pinned fixture, plus an append-only
manifest, written unconditionally.** `bench/evidence.py`, per trial:

- `changes.diff` — unified diff from fixture to post-trial tree, applying
  with `git apply -p1`. The fixture is tracked and the commands live in the
  spec, so fixture + diff + scorer reconstructs the trial. This is the
  artifact; the hash is the seal on it.
- `scorer.txt` — the scorer's stdout/stderr. Now that metrics can come from
  scorer stdout, discarding it would leave the published numbers unbacked
  one level up.
- one `manifest.jsonl` line — metrics, exit code, both hashes, and the paths
  above, **all relative to the evidence root**. An absolute `/Users/...`
  path is not third-party checkable.

`fixture_hash` is load-bearing, not decoration: a diff is only re-derivable
against a pinned base, and the fixture is tracked but mutable. Recording the
base's hash means a later fixture edit is *detectable* rather than silently
misleading.

**Two roots, deliberately separate.** `--work-root` stays scratch and stays
ignored (`experiments/*/trials/`); `--evidence-root` defaults to
`experiments/<spec name>/evidence` and is tracked. Ignoring scratch is
correct *because* it is no longer the only copy — that inversion is the fix.

**Unretained ⇒ unpublishable, mechanically.** A run with no evidence root
now appends a warning, and `is_publishable` is `not warnings`. This single
line is the most important change here: before it, a run that retained
nothing printed the same publishable-looking report as one that retained
everything.

**Metrics may come from the scorer's stdout.** Precedence: `metric_fn` →
JSON object on stdout → `success` from the exit code. `TrialRecord` gains
`metrics_source` and `metrics_source_reason`, for the same reason
`TokenCount` carries its method (invariant 3): an `exit_code` metric cannot
distinguish "the check passed" from "the scorer could not run", and a reader
needs that distinction. A scorer reached through `$VERITY_SPEC_DIR` can live
beside the spec instead of inside the fixture, where the agent under test
would be able to read it.

## Alternatives considered

| Option | Why not |
|---|---|
| Commit full post-trial trees | Correct but ~10 near-identical fixture copies per pilot, plus `__pycache__` noise. That reviewer pressure is exactly what got these directories ignored originally; a fix that recreates it will be re-ignored. |
| Manifest only (hashes + metrics) | An aggregate-shaped assertion at per-trial granularity. Re-checkable only for internal consistency — this is what already existed and what the audit found insufficient. |
| git-notes | Needs `refs/notes/*` fetch config a fresh clone lacks, and is invisible in the working tree. Contradicts the "survives without the tool installed" property `.verity/`'s format was chosen for. |
| Tarball per trial | Does not diff — the property `memory/store.py` names as the best debugging tool this project has. Binary blobs, unreviewable. |

## Result

`verity eval experiments/family_b_pilot_8_arbitrary_tiebreak/eval_spec.json`
reproduces the pilot and retains its evidence:

```
naive  (n=5)  tie_correct: mean=0.0 range=[0.0, 0.0]
verity (n=5)  tie_correct: mean=1.0 range=[1.0, 1.0]
  tie_correct: floor=[0.0, 0.0] verity_mean=1.0 -> likely_real_difference
  visible_pass: floor=[1.0, 1.0] verity_mean=1.0 -> indistinguishable_from_noise
  NOT PUBLISHABLE  (degenerate noise floor, as ADR-0022 also reported)
```

Same numbers as the hand-run pilot, and `tie_correct` now actually arrives
through the CLI (`metrics_source: "scorer_json"`), which finding 6 shows it
previously could not. Verified by hand, the way a third party would:

```
$ cp -r fixture_repo /tmp/verity_1 && cd /tmp/verity_1
$ git apply -p1 .../evidence/trials/verity_1/changes.diff
$ VERITY_SPEC_DIR=... python3 .../score_pilot8.py
{"visible_pass": 1.0, "tie_correct": 1.0}      # manifest published exactly this
```

`tests/unit/test_bench_evidence.py::test_the_retained_diff_re_derives_the_metric`
does this for all 10 trials on every run of the suite, and **`verity verify
<evidence-root>`** does it on demand for any committed evidence directory:

```
$ verity verify experiments/family_b_pilot_8_arbitrary_tiebreak/evidence
  ok    naive_1   success=1 tie_correct=0 visible_pass=1
  ...
  ok    verity_5  success=1 tie_correct=1 visible_pass=1
  All 10 trial(s) re-derived from the retained artifact.
```

**That command found two real defects the moment it first ran**, which is the
argument for it existing rather than leaving the property to a test:

1. The evidence was not self-describing. A scorer reached through
   `$VERITY_SPEC_DIR` cannot be re-run unless the evidence records where that
   directory was; nothing did, so every trial failed to locate its scorer.
   `report.json` now records `spec_dir` relative to the evidence root.
2. `git apply` resolves paths against the enclosing work tree's root, not the
   cwd. Run inside a repository -- which the default work root under
   `.verity/` is -- it matched nothing and **exited 0 having changed
   nothing**, so every trial was scored against an unmodified fixture and the
   reported reason (a scorer exit code) had nothing to do with the cause. The
   replay directory is now `git init`-ed so it is its own toplevel.

The second is the same shape as the findings this ADR and 0028/0029 record: a
collaborator reported success while doing less than asked.

## Consequences

- One existing test flipped: a run passing no evidence root is no longer
  publishable. It previously asserted the opposite and passed silently —
  which was the defect, not the test.
- `tests/integration/test_cli_eval.py` is new; there was no CLI test for
  `verity eval` at all, which is part of why findings 2 and 6 survived. It
  includes a `git check-ignore` assertion on the default evidence root —
  the only mechanism that keeps findings 2 and 3 from recurring, since a
  comment in `.gitignore` cannot enforce itself.
- Declared limits: only UTF-8 text is diffable, so undecodable files are
  listed in `unreproducible_files` and make the run unpublishable rather
  than being silently skipped; file modes, symlinks and empty directories
  are not captured.
- **Pilots 4/5/6 are not repaired by any of this.** Their headline metric is
  `tool_uses`, a property of an agent's behaviour; a scripted stand-in
  performs zero tool calls, so no fixture-and-scorer harness can regenerate
  it. Re-running them would re-derive only `success`, which was a 5/5
  ceiling in all three — the part that was never the finding. They are
  recorded as permanently unverifiable in `experiments/UNREPRODUCIBLE.md`;
  presenting a re-run as a reproduction would be the category error
  invariant 7 exists to prevent.

## The lesson worth keeping

ADR-0022 read as sufficient because it added a field named `artifact_hash`
and a docstring asserting the problem was solved, and its test checked that
two hashes were equal — a true statement about the hash function that says
nothing about re-derivability. An invariant about evidence cannot be
discharged by a field whose test never re-derives the number. This is the
same shape as [ADR-0021](0021-consistency-relation-inversion.md) and as T6's
finding, now applied to a repair rather than to a checker: **be suspicious
of a fix whose test cannot fail for the original reason.**
