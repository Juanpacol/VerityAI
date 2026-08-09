# Family B pilot: does Verity change a task's outcome?

Per `docs/BENCHMARK_PROTOCOL.md`'s Family B procedure and the plan approved
for this session. Family A (`docs/adr/0009-family-a-real-measurement.md`)
answered "how many tokens does pruning remove." This pilot asks a different
question: does having Verity's tools available change whether an agent
actually fixes a bug correctly.

**Status: designed and validated, trials in progress.** No comparison
number is final until every step below has run — see the publication rule
in `docs/BENCHMARK_PROTOCOL.md`.

## The fixture

Both `fixture_repo_task1/` and `fixture_repo_task2/` are copies of the same
small, self-contained, real (not toy-trivial) Python order-processing
service — models, pricing, inventory, a service layer tying them together,
and a deliberate distractor (`pricing_legacy.py`, an unused module with a
function of the *same name* as the one that actually needs fixing, so a
plain grep for the symbol finds two candidates and only one is wired into
`checkout`).

Two bugs exist in the service, each with its own objective, deterministic
pass/fail check (`pytest`, never a subjective judgment call):

- **Task 1 (pricing)**: `apply_discount` in `src/orders/pricing.py` adds
  the discount instead of subtracting it, so a discount code *raises* the
  total. `tests/test_pricing.py::test_discount_reduces_the_total` fails.
- **Task 2 (inventory)**: `reserve_stock` in `src/orders/inventory.py`
  overwrites `_reserved[sku]` on each call instead of accumulating, so
  reservations from an earlier order silently vanish and the same stock can
  be oversold. `tests/test_inventory.py::
  test_reservations_accumulate_across_orders_for_the_same_sku` fails.

**Each fixture directory has only ONE of the two bugs active** — the other
is already fixed. `fixture_repo_task1/` fails exactly
`test_pricing.py::test_discount_reduces_the_total` (`1 failed, 5 passed`);
`fixture_repo_task2/` fails exactly the inventory test, same shape. This
was not the first design: an earlier version shared one fixture with both
bugs live and pointed each task's prompt at the same directory. Every one
of the first 4 validation trials (run to test the *mechanism*, not to
produce real Family B data) fixed *both* bugs regardless of which task it
was given, because the prompt said "make `pytest tests/` pass" and the
agent — reasonably — ran the whole suite and fixed everything failing in
it. That collapsed the two intended tasks into one and would have made
every trial in both conditions succeed by construction, telling us nothing
about a difference between them. Splitting the fixture per task is the
fix; those 4 trials' transcripts still usefully confirmed the *mechanism*
(isolated working directories, `pytest`-based scoring, no cross-trial
contamination) — they are not used as data.

Both single-bug fixtures were verified by hand before any real trial ran:
each fails exactly its one target test and no others, and hand-applying the
correct one-line fix makes all six tests in that fixture pass.

## The two conditions

Identical task prompt, identical model, identical starting file state. The only
variable:

- **`alone`**: the prompt makes no mention of Verity. The agent investigates
  and fixes the bug however it normally would (grep, read files, reason).
- **`verity`**: the prompt additionally names the `verity` CLI (installed
  globally on this machine, `pip install -e .` from the harness repo) and
  suggests using `verity graph build` / `verity graph context "..."` /
  `verity graph find <symbol>` to locate the right code before editing,
  instead of relying on manual exploration alone.

Both conditions may use any other tool (Read, Bash, Grep, `pytest`) freely.
Neither is told the bug's nature beyond what the failing test already says.

## Method

For each (task, condition) pair: 5 independent trials, each starting from a
fresh copy of that task's fixture directory, each run in isolation (no
trial can see another's changes). A trial's outcome is `{"success": 1.0}`
if `pytest tests/` passes completely afterward, `{"success": 0.0}`
otherwise — evaluated by the harness running pytest directly, never by
asking the agent whether it succeeded.

Per `docs/BENCHMARK_PROTOCOL.md`: the noise floor (5 `alone` repeats, or 5
`verity` repeats, compared against each other) is established *before* the
two conditions are compared against each other, using
`verity noise-floor` (`bench/repetition.py`, see ADR-0010). A result outside
that floor is `likely_real_difference`; inside it is
`indistinguishable_from_noise` — reported as such, not as "a small effect,"
per the protocol's own wording.

## Known limitations of this pilot, stated up front

- **N=5 per cell is a floor-establishing minimum, not a large sample.**
  A pilot this size can detect a large effect or its absence; it cannot
  responsibly claim a precise effect size.
- **Two tasks, one domain.** Both bugs are in the same small fixture. This
  says something about *this kind* of task (a localized logic bug in a
  small, unfamiliar codebase with a same-named distractor) — not about
  Verity's effect on refactors, architecture decisions, or large codebases.
- **The `alone` condition is not prevented from discovering `verity` on its
  own** (it is on `PATH` and globally importable on this machine) — it is
  simply never told about it or prompted to use it. This mirrors a
  realistic "not currently using this tool" baseline rather than an
  artificially sandboxed one, and is disclosed rather than hidden.
- **Success here means "the failing test passes without breaking the
  others,"** not "the fix is idiomatic" or "no unrelated files were
  touched." A model could pass by being narrowly correct in a way a human
  reviewer would still want changed — the metric is deliberately narrow so
  it can be scored without a subjective judgment call.
