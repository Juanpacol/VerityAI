"""Rendering a `ReliabilityReport` for a terminal or an agent.

Shared by `security.py` and `architecture.py` — both produce the same
`Finding`/`ReliabilityReport` shape, so one renderer serves both rather than
each engine inventing its own text format. Violations are never displayed
without the total that was checked, the same rule `context/health.py` applies
to its own aggregate score: a bare list of problems with no denominator
invites reading "3 findings" as either "barely anything" or "everything is
broken" depending on mood, when the honest answer needs the scanned count.
"""

from verityai.core.models import ReliabilityReport


def render_report(
    report: ReliabilityReport,
    title: str = "RELIABILITY",
    caveats: list[str] | None = None,
) -> str:
    """Format a reliability report, violations first, always with its scope.

    `caveats` are printed after the findings, not folded into the messages —
    a caveat qualifies an entire *rule*, not one location, and repeating it
    per finding would bury the one thing most worth reading in a wall of
    identical text.
    """
    lines = [title, ""]

    if report.degraded_reason:
        lines.append(f"  degraded: {report.degraded_reason}")
        lines.append("")

    if not report.violations:
        lines.append(f"  No violations found ({report.files_scanned:,} files scanned).")
        return "\n".join(lines)

    for finding in report.violations:
        location = f"{finding.path}" if finding.path else "(no location)"
        lines.append(f"  [{finding.severity.upper()}] {finding.rule_name}  ({location})")
        lines.append(f"           {finding.message}")
        lines.append("")

    lines.append(
        f"  {len(report.violations)} violation(s) across {report.files_scanned:,} files scanned"
    )

    if caveats:
        lines.append("")
        for caveat in caveats:
            lines.append(f"  note: {caveat}")

    return "\n".join(lines)
