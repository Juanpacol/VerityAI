"""Hidden scorer for pilot 8 (arbitrary tie-break).

Lives beside the spec, not inside `fixture_repo/`, and is reached through
`$VERITY_SPEC_DIR`. That distinction is the whole point: a hidden check
placed inside the fixture is readable by the agent being measured, which
would not merely inconvenience the pilot but invalidate it.

Prints a JSON object on stdout, which `bench/trial.py::parse_metrics` reads
as the trial's metrics. Two metrics, deliberately separated:

- `visible_pass` -- the committed test suite, which states only "highest
  score wins". Both conditions can satisfy this; it is the ceiling four
  earlier pilots hit.
- `tie_correct` -- the arbitrary rule that appears nowhere in the code: on
  equal scores the *lower* `id` wins. The tie case is ordered with the
  higher id first, so the natural idiom `max(candidates, key=score)`
  returns the wrong candidate. That is `FailureMode.PLAUSIBLE_IDIOM_WRONG_ON_EDGE`
  -- the agent writes ordinary, defensible code that is wrong only on the
  edge the policy governs.

The exit code tracks `visible_pass` alone, so "the agent completed the
stated task" and "the agent got the unstated policy right" stay separable
in the record instead of collapsing into one pass/fail.
"""

import json
import subprocess
import sys
from pathlib import Path

trial_dir = Path.cwd()
sys.path.insert(0, str(trial_dir))

visible = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-q"],
    cwd=trial_dir,
    capture_output=True,
    text=True,
)
visible_pass = 1.0 if visible.returncode == 0 else 0.0

tie_correct = 0.0
try:
    from allocation.pick_winner import pick_winner

    # Higher id listed first: a first-match `max` picks id 7, the policy says id 3.
    winner = pick_winner([{"id": 7, "score": 50}, {"id": 3, "score": 50}])
    tie_correct = 1.0 if winner.get("id") == 3 else 0.0
except Exception:
    # A trial that cannot even be imported scores zero rather than crashing
    # the harness -- but `visible_pass` will already be 0, so the record
    # says which kind of failure this was.
    tie_correct = 0.0

print(json.dumps({"visible_pass": visible_pass, "tie_correct": tie_correct}))
raise SystemExit(0 if visible_pass == 1.0 else 1)
