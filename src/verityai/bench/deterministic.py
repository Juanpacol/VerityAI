"""Family A benchmarks: no model in the measured path.

Every number here is reproducible from the same input, so `n=1` suffices and
no noise floor is needed. That is the entire reason this module is separate
from anything stochastic — see `docs/BENCHMARK_PROTOCOL.md`. Mixing the two
families in one report lets a deterministic measurement lend its credibility
to a number that has not earned it.

The report refuses to describe a corpus it considers unrepresentative. A
transcript that is 90% exact duplicates will produce a spectacular reduction
figure that measures the fixture rather than the tool, so `CorpusReport`
carries a `warnings` list and the renderer prints it above the results rather
than below.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from verityai.context.classify import classify_all
from verityai.context.ingest import load
from verityai.context.prune import ContextPipeline
from verityai.context.tokenizer import TokenCounter
from verityai.core.models import ContextItem, PruneResult

# Above this share of duplicate tokens, a corpus tells you more about how it
# was generated than about the pipeline. Chosen as a round number that a
# hand-written synthetic fixture trips and a real transcript does not; it is a
# prompt to go look, not a hard threshold with a theory behind it.
_SUSPICIOUS_DUPLICATE_SHARE = 0.5

# A corpus this small cannot support any claim about typical behaviour.
_MIN_ITEMS_FOR_A_CLAIM = 20


@dataclass
class CaseResult:
    """One transcript, measured."""

    name: str
    items_before: int
    items_after: int
    tokens_before: int
    tokens_after: int
    token_method: str
    critical_retention: float
    digit_retention: float
    budget: int | None
    budget_met: bool
    stages: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def reduction_ratio(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return round(self.tokens_saved / self.tokens_before, 4)


@dataclass
class CorpusReport:
    """A set of cases, plus what can and cannot be concluded from them."""

    cases: list[CaseResult]
    token_method: str
    warnings: list[str] = field(default_factory=list)

    @property
    def total_before(self) -> int:
        return sum(case.tokens_before for case in self.cases)

    @property
    def total_after(self) -> int:
        return sum(case.tokens_after for case in self.cases)

    @property
    def total_saved(self) -> int:
        return self.total_before - self.total_after

    @property
    def aggregate_ratio(self) -> float:
        """Token-weighted reduction across the corpus.

        Weighted by tokens rather than averaging per-case ratios: a mean of
        ratios lets a tiny transcript with a 99% reduction outvote a large one
        with 10%, which is the opposite of what the number is meant to convey.
        """
        if self.total_before <= 0:
            return 0.0
        return round(self.total_saved / self.total_before, 4)

    @property
    def is_publishable(self) -> bool:
        """Whether this corpus can support an external claim.

        False when any warning fired. The publication rule in
        `docs/BENCHMARK_PROTOCOL.md` is mechanical on purpose — deciding case
        by case in the moment is how the retracted retry-loop claim got out.
        """
        return not self.warnings and not any(case.warnings for case in self.cases)


def measure_case(
    name: str,
    raw: str,
    task: str = "",
    budget: int | None = None,
    counter: TokenCounter | None = None,
) -> CaseResult:
    """Run the pipeline over one transcript and measure it."""
    from verityai.context.health import critical_retention, digit_retention

    counter = counter or TokenCounter()
    pipeline = ContextPipeline(counter=counter)

    items = load(raw)
    measured = [pipeline.measure(i, n) for n, i in enumerate(items)]
    # Two distinct classification passes, over two distinct item sets, each
    # answering a different question. Conflating them produced a real false
    # positive: measuring the pipeline over 5 real session transcripts
    # (see docs/BENCHMARK_PROTOCOL.md) reported "critical retention < 100%"
    # on three of them, which looked like the pipeline silently dropping
    # protected content -- the single invariant this whole engine exists to
    # guarantee. It wasn't. The lost items were exact duplicates of an
    # earlier critical item: an explicit marker's precedence over the
    # duplicate check (`classify.py`) means BOTH copies independently
    # classify CRITICAL when classified on the raw, pre-dedup list, but the
    # real pipeline dedups before classifying and correctly keeps only the
    # first copy -- the information survives once, which is the point of
    # deduplication. Classifying the raw list here counted both copies as
    # something the pipeline was obligated to keep twice.
    classified_raw = classify_all(measured)  # for the duplicate-share corpus check below
    classified_before = classify_all(pipeline.dedup(measured))  # ground truth for retention
    result: PruneResult = pipeline.run(items, task=task, budget=budget)

    warnings: list[str] = []

    duplicate_share = _duplicate_share(classified_raw)
    if duplicate_share > _SUSPICIOUS_DUPLICATE_SHARE:
        warnings.append(
            f"{duplicate_share:.0%} of tokens are exact duplicates -- this measures "
            "the corpus, not the pipeline. Do not publish this figure."
        )

    if len(items) < _MIN_ITEMS_FOR_A_CLAIM:
        warnings.append(
            f"only {len(items)} items; too small to support a claim about typical behaviour"
        )

    retention = critical_retention(classified_before, result.items)
    if retention < 1.0:
        warnings.append(
            f"BUG: critical retention is {retention:.1%}, must be 100%. "
            "The budget stage dropped a protected item."
        )

    figure_retention = digit_retention(classified_before, result.items)
    if figure_retention < 1.0:
        warnings.append(
            f"BUG: digit retention is {figure_retention:.1%}, must be 100%. "
            "A financial figure (amount/account number) was dropped."
        )

    return CaseResult(
        name=name,
        items_before=len(items),
        items_after=len(result.items),
        tokens_before=result.tokens_before,
        tokens_after=result.tokens_after,
        token_method=result.token_method,
        critical_retention=retention,
        digit_retention=figure_retention,
        budget=budget,
        budget_met=result.budget_met,
        stages=[stage.model_dump() for stage in result.stages],
        warnings=warnings,
    )


def _duplicate_share(items: list[ContextItem]) -> float:
    """Fraction of tokens sitting in exact duplicates of an earlier item."""
    total = sum(item.token_count for item in items)
    if total <= 0:
        return 0.0

    seen: set[str] = set()
    duplicated = 0
    for item in items:
        if item.content_hash in seen:
            duplicated += item.token_count
        else:
            seen.add(item.content_hash)
    return duplicated / total


def measure_corpus(
    paths: list[Path],
    task: str = "",
    budget: int | None = None,
    counter: TokenCounter | None = None,
) -> CorpusReport:
    """Measure every transcript in `paths` with one consistent counter.

    One counter for the whole corpus, not one per case: a report that mixed an
    exact count with an estimate would produce an aggregate that is an
    artefact of which files happened to be measured how.
    """
    counter = counter or TokenCounter()

    cases = [
        measure_case(
            path.name,
            path.read_text(encoding="utf-8"),
            task=task,
            budget=budget,
            counter=counter,
        )
        for path in paths
    ]

    warnings: list[str] = []
    if len(cases) < 3:
        warnings.append(f"only {len(cases)} transcript(s); not a corpus")
    if not counter.is_exact:
        warnings.append(
            f"token counts are estimates ({counter.method}); install tiktoken "
            "before publishing any figure"
        )

    return CorpusReport(cases=cases, token_method=counter.method, warnings=warnings)


def render_report(report: CorpusReport) -> str:
    """Format a corpus report, warnings first."""
    lines = ["DETERMINISTIC BENCHMARK (Family A -- no model in the measured path)", ""]

    if report.warnings or any(case.warnings for case in report.cases):
        lines.append("  WARNINGS")
        for warning in report.warnings:
            lines.append(f"    - {warning}")
        for case in report.cases:
            for warning in case.warnings:
                lines.append(f"    - [{case.name}] {warning}")
        lines.append("")

    has_budget = any(case.budget is not None for case in report.cases)
    budget_col = f" {'budget_met':>10}" if has_budget else ""
    lines.append(
        f"  {'case':<28} {'before':>10} {'after':>10} {'saved':>10} {'ratio':>8}{budget_col}"
    )
    for case in report.cases:
        row = (
            f"  {case.name:<28} {case.tokens_before:>10,} {case.tokens_after:>10,} "
            f"{case.tokens_saved:>10,} {case.reduction_ratio:>8.1%}"
        )
        if has_budget:
            row += f" {str(case.budget_met):>10}"
        lines.append(row)

    lines.extend(
        [
            "",
            f"  {'TOTAL':<28} {report.total_before:>10,} {report.total_after:>10,} "
            f"{report.total_saved:>10,} {report.aggregate_ratio:>8.1%}",
            "",
            f"  Counting method: {report.token_method}",
            f"  Critical retention: {min((c.critical_retention for c in report.cases), default=1.0):.1%} "
            "(must be 100%)",
            f"  Digit retention:    {min((c.digit_retention for c in report.cases), default=1.0):.1%} "
            "(must be 100%)",
        ]
    )

    if has_budget:
        unmet = [c.name for c in report.cases if not c.budget_met]
        if unmet:
            lines.append(
                f"  Budget met:         {len(report.cases) - len(unmet)}/{len(report.cases)} "
                "cases -- when a case's budget is not met, its critical/digit retention is "
                "100% by construction (the protected set is kept whole rather than cut), "
                "not evidence the ranking under pressure was accurate. See "
                "docs/BENCHMARK_PROTOCOL.md."
            )
        else:
            lines.append(f"  Budget met:         {len(report.cases)}/{len(report.cases)} cases")

    lines.append("")

    if report.is_publishable:
        lines.append("  This corpus meets the Family A publication bar.")
    else:
        lines.append(
            "  NOT PUBLISHABLE. Resolve the warnings above before quoting any "
            "figure externally. See docs/BENCHMARK_PROTOCOL.md."
        )

    return "\n".join(lines)


def to_json(report: CorpusReport) -> str:
    """Serialize a report, warnings included.

    The warnings travel with the data rather than living only in the rendered
    text, so a downstream consumer cannot pick up the numbers while leaving
    behind the reasons not to trust them.
    """
    return json.dumps(
        {
            "family": "A",
            "token_method": report.token_method,
            "publishable": report.is_publishable,
            "warnings": report.warnings,
            "totals": {
                "tokens_before": report.total_before,
                "tokens_after": report.total_after,
                "tokens_saved": report.total_saved,
                "reduction_ratio": report.aggregate_ratio,
            },
            "cases": [
                {
                    "name": case.name,
                    "items_before": case.items_before,
                    "items_after": case.items_after,
                    "tokens_before": case.tokens_before,
                    "tokens_after": case.tokens_after,
                    "tokens_saved": case.tokens_saved,
                    "reduction_ratio": case.reduction_ratio,
                    "critical_retention": case.critical_retention,
                    "digit_retention": case.digit_retention,
                    "budget": case.budget,
                    "budget_met": case.budget_met,
                    "warnings": case.warnings,
                    "stages": case.stages,
                }
                for case in report.cases
            ],
        },
        indent=2,
    )
