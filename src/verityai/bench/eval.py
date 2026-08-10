"""`verity eval`: the trial harness's publish-or-refuse layer.

Ties `bench/trial.py` (real trials, retained artifacts) to
`bench/repetition.py` (noise-floor statistics, unchanged since ADR-0010 --
it already accepts arbitrary `dict[str, float]` metrics) and copies the
refuse-to-publish discipline `bench/deterministic.py` established for
Family A: a `warnings` list, an `is_publishable` gate, and a renderer that
prints warnings above the numbers, never below.

The gates here answer a question Family A's gates don't need to: a
stochastic comparison can look conclusive and still be nearly meaningless.
Seven of this project's first nine Family B pilots had a `[0, 0]` or
`[1, 1]` noise floor for their headline metric (`docs/MEASUREMENTS.md`) --
every within-condition repeat landed on the exact same value, which makes
`compare_to_noise_floor` report `likely_real_difference` for *any*
between-condition value at all, real effect or not. `is_publishable` here
flags that condition explicitly rather than let a degenerate floor read as
a strong result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verityai.bench.evidence import read_manifest, write_run_evidence
from verityai.bench.repetition import compare_to_noise_floor, summarize_metric_variance
from verityai.bench.trial import metrics_by_condition, run_trials
from verityai.core.models import TrialRecord, TrialSpec

# Fewer trials than this per condition cannot support a published claim --
# matches the N>=5 this project's own Family B pilots have used throughout.
_MIN_TRIALS_FOR_A_CLAIM = 5


@dataclass
class EvalReport:
    """One `TrialSpec`, run and compared, with what can and cannot be
    concluded from it."""

    spec_name: str
    records: list[TrialRecord]
    baseline_condition: str
    comparisons: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Where the re-derivable artifact was written, or None for a run that
    # retained nothing. Reported rather than inferred, so a reader of the
    # report never has to guess whether evidence exists.
    evidence_root: str | None = None

    @property
    def is_publishable(self) -> bool:
        """False when any warning fired -- mechanical on purpose, the same
        reasoning `deterministic.py.CorpusReport.is_publishable` states:
        deciding case by case in the moment is how a bad number gets out.
        """
        return not self.warnings

    def artifact_hashes(self) -> dict[str, str]:
        """trial_id -> artifact_hash, the re-derivability record ADR-0021's
        sibling finding (Phase 0 truth repair) established every published
        trial number now needs."""
        return {record.trial_id: record.artifact_hash for record in self.records}


def run_eval(
    spec: TrialSpec,
    invoke_agent,
    work_root: Path,
    metric_fn=None,
    classify_failure=None,
    evidence_root: Path | None = None,
    spec_dir: Path | None = None,
) -> EvalReport:
    """Run every trial in `spec`, then compare every non-baseline condition
    against the first condition listed (the baseline / "within" repeat).

    `spec.conditions` order matters: `conditions[0]` is the configuration
    repeated to establish the noise floor (`docs/BENCHMARK_PROTOCOL.md`
    step 1); every other condition is compared against it (steps 2-4).

    `evidence_root` is where the re-derivable artifact is written. Omitting
    it is allowed -- an in-process caller exploring a spec has no reason to
    litter the tree -- but such a run is reported as not publishable, which
    is the honest outcome rather than a special case.
    """
    records = run_trials(
        spec,
        invoke_agent,
        work_root,
        metric_fn=metric_fn,
        classify_failure=classify_failure,
        evidence_root=evidence_root,
        spec_dir=spec_dir,
    )
    grouped = metrics_by_condition(records)
    baseline = spec.conditions[0]
    baseline_repeats = grouped.get(baseline, [])

    warnings: list[str] = []
    if spec.n < _MIN_TRIALS_FOR_A_CLAIM:
        warnings.append(
            f"n={spec.n} per condition; need >= {_MIN_TRIALS_FOR_A_CLAIM} to support a claim"
        )

    # The gate that gives invariant 7 teeth. Before this, a run that retained
    # nothing printed the same publishable-looking report as one that
    # retained everything -- which is how three pilots' numbers outlived
    # their evidence (ADR-0027).
    if evidence_root is None:
        warnings.append(
            "no evidence root: this run retained no re-derivable artifact, so its "
            "numbers must not be published (invariant 7, CLAUDE.md)"
        )
    else:
        entries = read_manifest(evidence_root)
        undecodable = {
            file["path"] for entry in entries for file in entry.get("unreproducible_files", [])
        }
        if undecodable:
            warnings.append(
                f"{len(undecodable)} file(s) could not be represented in a diff "
                f"({', '.join(sorted(undecodable)[:3])}...): the retained artifact cannot "
                "fully reconstruct these trials"
            )

    for record in records:
        if not record.artifact_hash:
            warnings.append(f"trial {record.trial_id!r} has no artifact hash -- unretained result")

    # A spec can ask for a metric the scorer never reports -- which used to
    # print `insufficient_data` inside an otherwise publishable report.
    # "We measured nothing" and "we measured nothing and said so" are
    # different claims.
    for metric in spec.metric_keys:
        missing = [r.trial_id for r in records if metric not in r.metrics]
        if missing:
            warnings.append(
                f"metric {metric!r} was not reported for {len(missing)} of {len(records)} "
                "trials; have the scorer print a JSON object on stdout, or pass a "
                "metric_fn -- comparisons for it are absent, not null results"
            )

    comparisons: dict[str, dict[str, dict[str, Any]]] = {}
    for condition in spec.conditions[1:]:
        between_repeats = grouped.get(condition, [])
        per_metric: dict[str, dict[str, Any]] = {}
        for metric in spec.metric_keys:
            result = compare_to_noise_floor(baseline_repeats, between_repeats, metric)
            per_metric[metric] = result

            floor_min = result.get("noise_floor_min")
            floor_max = result.get("noise_floor_max")
            if floor_min is not None and floor_min == floor_max:
                warnings.append(
                    f"{metric!r} noise floor for {baseline!r} is degenerate "
                    f"([{floor_min}, {floor_max}]) -- every repeat landed on the same "
                    "value, so any between-condition difference reads as "
                    "'likely_real_difference' regardless of whether it is one. "
                    "Treat this comparison as suggestive, not conclusive."
                )
        comparisons[condition] = per_metric

    report = EvalReport(
        spec_name=spec.name,
        records=records,
        baseline_condition=baseline,
        comparisons=comparisons,
        warnings=warnings,
        evidence_root=str(evidence_root) if evidence_root is not None else None,
    )

    # Written last and unconditionally: the spec and the report belong beside
    # the per-trial evidence, or a reader has the numbers without the thing
    # that produced them.
    if evidence_root is not None:
        write_run_evidence(evidence_root, spec, to_json(report))

    return report


def render_report(report: EvalReport) -> str:
    """Format an eval report, warnings first -- mirrors
    `bench/deterministic.py::render_report`'s layout."""
    lines = [f"EVAL: {report.spec_name}", ""]

    if report.warnings:
        lines.append("  WARNINGS")
        for warning in report.warnings:
            lines.append(f"    - {warning}")
        lines.append("")

    grouped = metrics_by_condition(report.records)
    for condition, repeats in grouped.items():
        summary = summarize_metric_variance(repeats)
        lines.append(f"  {condition} (n={summary['n_repeats']}):")
        for key, stats in summary.items():
            if key == "n_repeats":
                continue
            lines.append(
                f"    {key}: mean={stats['mean']} range=[{stats['min']}, {stats['max']}] "
                f"n={stats['n']}"
            )
    lines.append("")

    for condition, per_metric in report.comparisons.items():
        lines.append(f"  {report.baseline_condition} (baseline) vs {condition}:")
        for metric, result in per_metric.items():
            if result["conclusion"] == "insufficient_data":
                lines.append(f"    {metric}: insufficient_data -- {result['reason']}")
            else:
                lines.append(
                    f"    {metric}: floor=[{result['noise_floor_min']}, "
                    f"{result['noise_floor_max']}] {condition}_mean={result['between_config_mean']} "
                    f"-> {result['conclusion']}"
                )
    lines.append("")

    failure_counts: dict[str, int] = {}
    for record in report.records:
        if record.failure_mode is not None:
            failure_counts[record.failure_mode.value] = (
                failure_counts.get(record.failure_mode.value, 0) + 1
            )
    if failure_counts:
        lines.append("  Failure modes:")
        for mode, count in sorted(failure_counts.items()):
            lines.append(f"    {mode}: {count}")
        lines.append("")

    if report.evidence_root:
        lines.append(f"  Evidence retained in {report.evidence_root}")
        lines.append(
            "    per trial: changes.diff (git apply -p1 onto a fresh fixture copy), "
            "scorer.txt, and a manifest.jsonl line pinned to the fixture's hash."
        )
    else:
        lines.append("  Evidence retained: NONE -- no evidence root was given.")
    lines.append("")

    if report.is_publishable:
        lines.append("  This eval run meets the publication bar.")
    else:
        lines.append(
            "  NOT PUBLISHABLE. Resolve the warnings above before quoting any "
            "figure externally. See docs/BENCHMARK_PROTOCOL.md and invariant 7 "
            "(CLAUDE.md)."
        )

    return "\n".join(lines)


def to_json(report: EvalReport) -> dict[str, Any]:
    """Serialize an eval report, warnings and artifact hashes included --
    same reasoning as `deterministic.py::to_json`: a downstream consumer
    must not be able to pick up the numbers while leaving behind the
    reasons not to trust them, or the artifact that would let them check."""
    return {
        "spec_name": report.spec_name,
        "baseline_condition": report.baseline_condition,
        "publishable": report.is_publishable,
        "warnings": report.warnings,
        "evidence_root": report.evidence_root,
        "comparisons": report.comparisons,
        "trials": [
            {
                "trial_id": record.trial_id,
                "condition": record.condition,
                "scorer_exit_code": record.scorer_exit_code,
                "metrics": record.metrics,
                "metrics_source": record.metrics_source,
                "metrics_source_reason": record.metrics_source_reason,
                "artifact_hash": record.artifact_hash,
                "failure_mode": (
                    record.failure_mode.value if record.failure_mode is not None else None
                ),
            }
            for record in report.records
        ],
    }
