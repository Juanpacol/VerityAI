"""Hidden scorer for pilot 7 (domain ambiguity).

Lives beside the spec, not inside `fixture_repo/`, and is reached through
`$VERITY_SPEC_DIR` -- the same reason pilot 8's scorer does, see
`../family_b_pilot_8_arbitrary_tiebreak/score_pilot8.py`: a hidden check
placed inside the fixture is readable by the agent being measured.

Two metrics, matching the pilot's original hand-scored results
(`naive_results.json`, `verity_results.json`):

- `visible_pass` -- the committed test suite, which only exercises the
  unambiguous case (20 days overdue against a 10-day grace period). Both
  conditions were designed to pass this; it is the ceiling four earlier
  pilots also hit.
- `boundary_correct` -- the fabricated phase-A policy, which appears
  nowhere in the code: grace is inclusive, so a fee applies only when
  `days_overdue > grace_days`, strictly. `calculate_invoice` at exactly
  the boundary (`days_overdue == grace_days == 10`) must return the
  subtotal unchanged (no fee) to score correct.

The exit code tracks `visible_pass` alone, matching pilot 8's convention.
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

boundary_correct = 0.0
try:
    from billing.invoice import calculate_invoice

    order = {"subtotal": 1000.0, "days_overdue": 10, "grace_days": 10}
    boundary_correct = 1.0 if calculate_invoice(order) == 1000.0 else 0.0
except Exception:
    # A trial that cannot even be imported scores zero rather than crashing
    # the harness -- but `visible_pass` will already be 0, so the record
    # says which kind of failure this was.
    boundary_correct = 0.0

print(json.dumps({"visible_pass": visible_pass, "boundary_correct": boundary_correct}))
raise SystemExit(0 if visible_pass == 1.0 else 1)
