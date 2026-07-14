"""Unit tests for compliance/report_generator.py."""

from verityai.compliance.report_generator import (
    build_compliance_report,
    build_compliance_report_from_trace,
    export_to_pdf,
    export_to_sarif,
)
from verityai.ontology.models import (
    Counterexample,
    GenerationResponse,
    ReasoningTrace,
    VerificationResult,
    VerificationStatus,
)


def make_trace(status=VerificationStatus.PASS, violations=None, kg_context=None):
    return ReasoningTrace(
        user_prompt="write a divide function",
        generated_code="def divide():\n    a = 10\n    b = 2\n    assert b != 0\n    return a // b\n",
        attempt_number=1,
        kg_context=kg_context
        or {"rules": [{"name": "no_div_by_zero", "description": "..."}], "patterns": []},
        llm_reasoning="reasoning",
        verification_result=VerificationResult(
            code_id="",
            status=status,
            confidence=0.9,
            violations=violations or [],
            z3_result=status.value,
        ),
        confidence_score=0.9,
    )


def make_response(status="success", trace=None):
    trace = trace or make_trace()
    return GenerationResponse(
        code=trace.generated_code,
        language="python",
        traces=[trace],
        final_verification=trace.verification_result,
        confidence=trace.confidence_score,
        explanation="explanation text",
        status=status,
    )


class TestBuildComplianceReport:
    def test_captures_rules_applied_and_status(self):
        response = make_response()
        report = build_compliance_report(response)

        assert report.rules_applied == ["no_div_by_zero"]
        assert report.final_status == "success"
        assert report.verification_status == "pass"
        assert report.code == response.code

    def test_no_traces_yields_empty_prompt_and_no_trace_id(self):
        response = GenerationResponse(
            code="",
            language="python",
            traces=[],
            final_verification=VerificationResult(
                code_id="", status=VerificationStatus.FAIL, confidence=0.0
            ),
            confidence=0.0,
            explanation="LLM unreachable",
            status="failed",
        )
        report = build_compliance_report(response)

        assert report.trace_id is None
        assert report.user_prompt == ""
        assert report.rules_applied == []


class TestBuildComplianceReportFromTrace:
    def test_pass_status_maps_to_success(self):
        trace = make_trace(status=VerificationStatus.PASS)
        report = build_compliance_report_from_trace(trace)
        assert report.final_status == "success"

    def test_not_verified_maps_to_partial(self):
        trace = make_trace(status=VerificationStatus.NOT_VERIFIED)
        report = build_compliance_report_from_trace(trace)
        assert report.final_status == "partial"

    def test_fail_maps_to_failed(self):
        trace = make_trace(status=VerificationStatus.FAIL)
        report = build_compliance_report_from_trace(trace)
        assert report.final_status == "failed"

    def test_includes_violations(self):
        violation = Counterexample(input_values={"x": 0}, description="div by zero")
        trace = make_trace(status=VerificationStatus.FAIL, violations=[violation])
        report = build_compliance_report_from_trace(trace)
        assert len(report.violations) == 1
        assert report.violations[0].description == "div by zero"


class TestExportToSarif:
    def test_includes_rule_definitions(self):
        report = build_compliance_report(make_response())
        sarif = export_to_sarif(report)

        assert sarif["version"] == "2.1.0"
        rule_names = [r["name"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
        assert "no_div_by_zero" in rule_names

    def test_clean_run_gets_one_informational_result(self):
        report = build_compliance_report(make_response())
        sarif = export_to_sarif(report)
        results = sarif["runs"][0]["results"]

        assert len(results) == 1
        assert results[0]["level"] == "note"

    def test_violations_become_error_level_results(self):
        violation = Counterexample(input_values={"x": 0}, description="div by zero", source_line=3)
        trace = make_trace(status=VerificationStatus.FAIL, violations=[violation])
        report = build_compliance_report_from_trace(trace)
        sarif = export_to_sarif(report)
        results = sarif["runs"][0]["results"]

        assert len(results) == 1
        assert results[0]["level"] == "error"
        assert results[0]["message"]["text"] == "div by zero"
        assert results[0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 3

    def test_is_json_serializable(self):
        import json

        report = build_compliance_report(make_response())
        sarif = export_to_sarif(report)
        json.dumps(sarif)  # must not raise


class TestExportToPdf:
    def test_produces_valid_pdf_bytes(self):
        report = build_compliance_report(make_response())
        pdf_bytes = export_to_pdf(report)

        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_handles_empty_rules_and_violations(self):
        trace = make_trace(kg_context={})
        report = build_compliance_report_from_trace(trace)
        pdf_bytes = export_to_pdf(report)
        assert pdf_bytes.startswith(b"%PDF")

    def test_handles_special_characters_in_prompt_and_code(self):
        """Prompt/code with <, >, & must not break Paragraph's XML-like markup."""
        trace = ReasoningTrace(
            user_prompt="if a < b and c > d: do <this> & that",
            generated_code="if a < b:\n    x = a & b\n    assert x <= a\n",
            attempt_number=1,
            kg_context={"rules": [{"name": "rule<with>special&chars"}]},
            llm_reasoning="",
            verification_result=VerificationResult(
                code_id="", status=VerificationStatus.PASS, confidence=1.0
            ),
            confidence_score=1.0,
        )
        report = build_compliance_report_from_trace(trace)
        pdf_bytes = export_to_pdf(report)
        assert pdf_bytes.startswith(b"%PDF")
