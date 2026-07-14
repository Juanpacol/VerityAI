"""Compliance/audit-trail report generation (Phase 4 Part B).

Builds a ComplianceReport from a GenerationResponse -- the human-facing
artifact an enterprise compliance/security reviewer consumes (rules
applied, verification proof, confidence), as distinct from the
developer-facing GenerationResponse itself. Exports to SARIF (machine-
readable, consumed by CI/CD and code-scanning tools) and PDF (for an
audit binder or a reviewer who never opens a terminal).
"""

from io import BytesIO
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

from verityai.ontology.models import (
    ComplianceReport,
    GenerationResponse,
    ReasoningTrace,
    VerificationStatus,
)


def build_compliance_report(response: GenerationResponse) -> ComplianceReport:
    """Build a ComplianceReport from a full GenerationResponse's trace history.

    Rules/patterns are read from the final attempt's kg_context, which the
    orchestrator's retry loop constructs once per request and reuses
    across attempts (see Orchestrator._fetch_kg_context) -- so any of the
    traces carries the same rules_applied/patterns_reviewed.
    """
    final_trace = response.traces[-1] if response.traces else None

    return ComplianceReport(
        trace_id=final_trace.id if final_trace else None,
        user_prompt=final_trace.user_prompt if final_trace else "",
        language=response.language,
        final_status=response.status,
        confidence=response.confidence,
        attempt_count=len(response.traces),
        rules_applied=_rule_names(final_trace),
        patterns_reviewed=_pattern_names(final_trace),
        verification_status=response.final_verification.status.value,
        verification_z3_result=response.final_verification.z3_result,
        violations=list(response.final_verification.violations),
        code=response.code,
    )


def build_compliance_report_from_trace(
    trace: ReasoningTrace, language: str = "python"
) -> ComplianceReport:
    """Build a ComplianceReport from a single persisted ReasoningTrace.

    Used when only a stored trace is available (e.g. the API's
    GET /trace/{id}/compliance-report), not the full in-memory
    GenerationResponse from the original request -- TraceStore persists
    individual ReasoningTrace rows, not the response wrapper. final_status
    is inferred from the trace's own verification result using the same
    PASS/NOT_VERIFIED/else mapping Orchestrator._build_response uses.
    """
    verification = trace.verification_result
    if verification is None:
        final_status = "failed"
        verification_status = VerificationStatus.FAIL.value
        z3_result = None
        violations = []
    else:
        if verification.status == VerificationStatus.PASS:
            final_status = "success"
        elif verification.status == VerificationStatus.NOT_VERIFIED:
            final_status = "partial"
        else:
            final_status = "failed"
        verification_status = verification.status.value
        z3_result = verification.z3_result
        violations = list(verification.violations)

    return ComplianceReport(
        trace_id=trace.id,
        user_prompt=trace.user_prompt,
        language=language,
        final_status=final_status,
        confidence=trace.confidence_score,
        attempt_count=trace.attempt_number,
        rules_applied=_rule_names(trace),
        patterns_reviewed=_pattern_names(trace),
        verification_status=verification_status,
        verification_z3_result=z3_result,
        violations=violations,
        code=trace.generated_code,
    )


def _rule_names(trace: Optional[ReasoningTrace]) -> list[str]:
    if trace is None:
        return []
    return [r.get("name", "") for r in trace.kg_context.get("rules", [])]


def _pattern_names(trace: Optional[ReasoningTrace]) -> list[str]:
    if trace is None:
        return []
    return [p.get("name", "") for p in trace.kg_context.get("patterns", [])]


def export_to_sarif(report: ComplianceReport) -> dict:
    """Render a ComplianceReport as a SARIF 2.1.0 log (dict, JSON-serializable).

    Each rule_applied becomes a SARIF rule definition; each violation
    becomes a SARIF result. A clean run (no violations) still emits one
    informational result stating the verification outcome, rather than an
    empty results array that could read as "nothing was checked."
    """
    rules = [
        {"id": f"verityai-rule-{i + 1}", "name": name, "shortDescription": {"text": name}}
        for i, name in enumerate(report.rules_applied)
        if name
    ]

    results = []
    for i, violation in enumerate(report.violations):
        results.append(
            {
                "ruleId": f"verityai-violation-{i + 1}",
                "level": "error",
                "message": {"text": violation.description},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "generated_code.py"},
                            "region": {"startLine": violation.source_line or 1},
                        }
                    }
                ],
            }
        )

    if not results:
        results.append(
            {
                "ruleId": "verityai-verification",
                "level": "note",
                "message": {
                    "text": (
                        f"Verification status: {report.verification_status} "
                        f"(confidence {report.confidence:.1%})"
                    )
                },
                "locations": [
                    {"physicalLocation": {"artifactLocation": {"uri": "generated_code.py"}}}
                ],
            }
        )

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "VerityAI", "version": "0.0.1", "rules": rules}},
                "results": results,
            }
        ],
    }


def export_to_pdf(report: ComplianceReport) -> bytes:
    """Render a ComplianceReport as a PDF document (bytes)."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title="VerityAI Compliance Report")
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("VerityAI Compliance Report", styles["Title"]))
    story.append(Spacer(1, 12))

    metadata_rows = [
        ["Report ID", str(report.id)],
        ["Generated At", report.generated_at.isoformat()],
        ["Prompt", escape(report.user_prompt)],
        ["Language", report.language],
        ["Status", report.final_status],
        ["Verification Status", report.verification_status],
        ["Confidence", f"{report.confidence:.1%}"],
        ["Attempts", str(report.attempt_count)],
    ]
    table = Table(
        [[Paragraph(escape(str(cell)), styles["Normal"]) for cell in row] for row in metadata_rows],
        colWidths=[140, 340],
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("Rules Applied", styles["Heading2"]))
    if report.rules_applied:
        for rule in report.rules_applied:
            story.append(Paragraph(f"• {escape(rule)}", styles["Normal"]))
    else:
        story.append(Paragraph("None recorded.", styles["Normal"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Patterns Reviewed", styles["Heading2"]))
    if report.patterns_reviewed:
        for pattern in report.patterns_reviewed:
            story.append(Paragraph(f"• {escape(pattern)}", styles["Normal"]))
    else:
        story.append(Paragraph("None recorded.", styles["Normal"]))
    story.append(Spacer(1, 12))

    if report.violations:
        story.append(Paragraph("Violations", styles["Heading2"]))
        for violation in report.violations:
            story.append(Paragraph(escape(violation.description), styles["Normal"]))
        story.append(Spacer(1, 12))

    story.append(Paragraph("Generated Code", styles["Heading2"]))
    story.append(Preformatted(report.code, styles["Code"]))

    doc.build(story)
    return buffer.getvalue()
