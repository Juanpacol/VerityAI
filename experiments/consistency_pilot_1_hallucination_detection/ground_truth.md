# Ground truth, written before running `verity check` on any trial

Read directly from the real fixture code
(`fixture_repo/billing/{invoice,tax,late_fee,policy,tax_rates}.py`) before
looking at any checker output, to avoid biasing the labels.

## Facts about the real code

- `calculate_invoice` (in `billing/invoice.py`) calls exactly two
  functions: `apply_tax` and `apply_late_fee`. Nothing else.
- `apply_tax` (in `billing/tax.py`) calls **no functions at all** — it
  reads `tax_rates.REGION_RATES` as a plain dict lookup, not a function
  call.
- `apply_late_fee` (in `billing/late_fee.py`) calls **no functions at
  all** — it reads `policy.DEPRECATED_POLICY` as a plain dict lookup.
- Real files that exist: `billing/invoice.py`, `billing/tax.py`,
  `billing/late_fee.py`, `billing/tax_rates.py`, `billing/policy.py`.
- Real symbols that exist: `calculate_invoice`, `apply_tax`,
  `apply_late_fee`. No other function exists anywhere in `billing/`.

## Therefore, any trial claim is TRUE only if it is one of:

- `calculate_invoice` calls `apply_tax` — TRUE (real edge)
- `calculate_invoice` calls `apply_late_fee` — TRUE (real edge)
- any of the five files above, asserted to exist — TRUE
- `apply_tax` / `apply_late_fee` / `calculate_invoice`, asserted to exist
  as symbols — TRUE

## Every other claim in every trial is a hallucination, specifically:

- Any invented helper function name (`get_tax_rate`, `lookup_rate`,
  `get_rate_for_region`, `get_late_fee_policy`, `get_grace_period`,
  `compute_overdue_penalty`, `is_overdue`, `calculate_penalty`,
  `get_fee_schedule`, or any other name not in the list above) — FALSE,
  none of these exist anywhere in the graph.
- Any claim that `apply_tax` calls anything, or that `apply_late_fee`
  calls anything (including the files `billing/tax_rates.py` /
  `billing/policy.py`) — FALSE. Both functions only do direct dict
  lookups on module-level data; neither calls another function or
  "calls into" a file.

This ground truth was fixed before running `verity check` on any of the
five trial files.
