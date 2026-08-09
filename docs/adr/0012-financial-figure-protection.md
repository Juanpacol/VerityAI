# ADR-0012: Automatic protection for financial figures, and a real false positive

- **Status**: Accepted
- **Date**: 2026-08-09
- **Context**: the numeric-precision plan approved this session — the user's
  observation that exact figures (amounts, account numbers) are a domain
  where context loss is both high-stakes and, unlike the previous Family B
  pilot's code bugs, has no subjective middle ground: a digit either
  survives verbatim or it doesn't.

## Context

Before this change, `context/classify.py` had no concept of "an exact
number worth protecting." A financial figure mentioned once in a long
conversation, without an explicit `verity:critical` marker, had no special
protection — it could be silently cut by `compress` (which elides the
middle of long tool output) or by `budget` (which drops non-critical items)
with nothing to indicate it had ever been there.

## Decision 1: one shared extractor, two narrow patterns, deliberately

`extract_financial_figures()` in `classify.py` is used by both the new
classification rule and the new `digit_retention()` metric in `health.py` —
one pattern, two consumers, so protecting a figure and measuring whether it
survived can never quietly drift apart.

Two patterns only, both requiring a real signal beyond "this is a number":

- **Currency amount**: a currency symbol (`$€£¥`) or ISO code (`USD`,
  `EUR`, ...) immediately preceding the digits. A bare number is never
  enough.
- **IBAN-shaped account number**: two letters, two digits, then 10-30
  alphanumerics — close to a real IBAN's 15-34 character total, not
  "anything vaguely shaped like one."

Deliberately excluded: bare percentages (`92.4%` is routine noise in this
project's own benchmark output), line numbers, counts ("100 tests
passed"), generic digit runs. Matching any of those would make nearly
everything in a typical dev conversation CRITICAL and defeat pruning
entirely — the same "narrow verifiable subset" discipline as ADR-0001,
applied to a new problem.

The new rule sits in `_decide()` at the same precedence as an explicit
marker: before duplication (a repeated figure is still, on its own, worth
protecting — same reasoning `CRITICAL_MARKERS` already uses) and before
recency (a figure mentioned once, early, must not depend on staying
"recent" to survive).

## Decision 2: a real false positive, found by running this against real data

Running the finished rule against the same 5 real Claude Code session
transcripts used in ADR-0009 immediately produced a wrong match: a
base64-ish blob (`...bsIT9IZ+CH78K2XZ+KRGYuWie...`, part of an encoded
attachment or diff) matched the IBAN pattern on the `CH78K2XZ` fragment.
The cause: regex `\b` treats `+` and `/` as word boundaries, so a pattern
anchored only with `\b` finds "boundaries" inside a single continuous
base64 run that no human would call a boundary at all.

Fixed with a lookaround excluding base64's own alphabet (`A-Za-z0-9+/=`)
immediately before and after the match, rather than relying on `\b` alone.
Raising the minimum BBAN length from 4 to 10 characters (closer to a real
IBAN's actual range) also helped, but the lookaround is what actually rules
out the base64 case — a longer minimum alone does not, since a large
enough blob still contains 10+ character alphanumeric runs by chance.
A regression test (`test_a_base64_style_blob_is_not_mistaken_for_an_iban`)
reproduces the exact string that triggered this.

This is the same category of finding as ADR-0009 and ADR-0011: a real
defect in the new mechanism, found only by running it against real data
before publishing anything, not by inspection.

## Result: measured on the real corpus, both configurations

Same 3 substantial real sessions as ADR-0009 (the two trivial ones — a
`/clear` and a `/model` command — excluded as before, too small to
support a claim):

| Configuration | Digit retention | Critical retention |
|---|---:|---:|
| No budget | 100% | 100% |
| 30,000-token budget | 100% | 100% |

21 distinct financial figures were found across the corpus (not zero — the
measurement is real, not vacuous) and every one survived, in both
configurations. Stated honestly: this corpus is software-development
conversation, not a financial domain, and most of the figures found are
literally example amounts inside this project's own docstrings and test
fixtures written during this same session (`$4,231.50`, `$500.00`, `EUR
1500` — all authored as illustrative examples, not genuine user financial
data). A 100% result here is evidence the mechanism works on what this
corpus happens to contain; it is not evidence about a genuinely
adversarial financial-domain conversation, where a figure sits once amid
heavy unrelated noise with decoy numbers nearby. That is what Phase 2 (a
purpose-built fixture, next) tests instead of assuming.

## Consequences

- Zero new dependencies; `re` is standard library, as everything in
  `classify.py` already was.
- The rule changes real classification behavior — a plain sentence
  containing `$500` or an IBAN-shaped string is now `CRITICAL` where it
  previously wasn't. Running `verity reliability security` and `verity
  reliability architecture` against this project's own source after the
  change showed no new findings, confirming the rule doesn't misfire on
  this codebase's own code and docs.
- `digit_retention` joins `critical_retention` as a second per-case
  publication gate in `bench/deterministic.py` — a corpus with a real
  digit-drop now fails the publication bar the same way a critical-item
  drop already did.
