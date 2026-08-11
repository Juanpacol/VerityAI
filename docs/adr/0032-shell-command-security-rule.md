# ADR-0032: a third security rule, chosen to pay for the low tier

- **Status**: accepted
- **Date**: 2026-08-11
- **Context**: `verity reliability risk --show-rules` printed `low 0/2 --
  nothing` — both built-in security rules were medium/high tier, so a
  low-tier file earned no scrutiny at all. ADR-0026 shipped that gap
  honestly (reporting it rather than hiding it behind a gate that would run
  zero rules and print no violations), but a gap reported is still a gap.

## The rule

`shell-command-injection`: a `subprocess.run`/`call`/`check_call`/
`check_output`/`Popen` call passed `shell=True` with a command built by
concatenation, f-string, `%`-format, `.format()`, or a bare variable —
`extract_shell_command_facts` in `analysis/facts.py`, reusing
`_is_dynamic_sql_string`'s already-written recognizers for "this string
wasn't a plain literal," since a shell command built the same ways a SQL
query is built is unsafe for the same reason. The safe idiom
(`shell_disabled_or_args_list`) is a call with no `shell=True` and the
command passed as a list, mirroring how `uses_parameterized_query` plays the
same role for the SQL rule.

**`severity="high"`, `risk_tier="low"` — deliberately not the same value.**
The two axes were confounded in the first two rules (`sql-injection` is
high/high, `check-then-act-race` is medium/medium), which made it easy to
assume they always move together. They don't: a shell-injection bug is
exactly as bad wherever it occurs (impact does not know about blast radius),
but running the check is cheap and worth doing even on a leaf file `risk.py`
would otherwise tier low for lack of any elevating signal. That's what a
low-tier rule *is* — not a lower-severity rule, one whose cost of checking
doesn't depend on the file's position in the graph.

## The predicted finding, found

Adding the rule and scanning this repository immediately flagged
`src/verityai/bench/trial.py` twice — `subprocess.run(spec.scorer_command,
shell=True, ...)` and `subprocess.run(command, shell=True, ...)` in
`command_invoker`, both passing a variable, not a literal. This was expected
before the rule was written: `bench/trial.py` is the harness that runs a
`TrialSpec`'s `scorer_command` and `condition_commands`, which are shell
commands *by design* — `docs/adr/0022-verity-eval-harness.md` states the
scorer "runs as a real subprocess," and arbitrary shell commands are the
supported way to plug in a scorer or a condition.

**Accepted, not fixed, and that is the correct call — not a compromise.**
The rule's own caveat (`RULE_CAVEATS["shell-command-injection"]`) names
exactly this case: no data-flow analysis means the extractor cannot tell a
command built from external, untrusted input apart from one built from a
`TrialSpec` the operator running `verity eval` authored and trusts. A
`TrialSpec` is not an attacker-controlled input channel — it is a file the
person running the harness wrote, the same trust boundary a shell script
they'd write by hand would have. Removing `shell=True` here would mean
`bench/trial.py` could no longer run arbitrary shell commands as scorers and
condition commands, which is the feature, not a bug in it.

**Why this is worth stating plainly rather than adjusting the rule to stay
quiet.** T6 (`docs/RESEARCH_FINDINGS_LEGACY.md`) found a checker that could
structurally never return `FAIL` — the mistake this project is most
careful never to repeat is shipping a rule that never fires and calling that
evidence of safety. A rule that finds something real in its own repository
on day one, correctly, and is then accepted with a stated reason, is the
opposite of that failure mode. Silently narrowing the extractor to make this
finding disappear would have reproduced the T6 mistake in miniature.

## Consequence

`verity reliability risk --show-rules` now prints `low 1/3` instead of
`low 0/2 -- nothing`. The gap is smaller, not closed — `sql-injection` and
`check-then-act-race` remain medium/high, so a low-tier file still earns
only one of three rules. `rules_for_tier` needed no change; tier gating was
already free once a low-tier rule existed (ADR-0026).
