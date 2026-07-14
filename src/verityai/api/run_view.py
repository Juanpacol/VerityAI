"""Server-rendered reasoning-trace view: backs GET /runs/{request_id}/view.

Server-rendered (unlike api/dashboard.py's client-side fetch() pattern)
because the Z3 constraint panel needs SymbolicDebugger(code).debug(...),
which is Python-only -- there is no client-side equivalent to call. No
<script> tags at all: everything here is static markup built from data
already available server-side, which also keeps the self-containment
story trivial (nothing to fetch, nothing to escape twice).

Categorical colors for the confidence-factor bar (--series-aqua/blue/
yellow/magenta) were chosen and ordered via the dataviz skill's palette
validator (CVD-safe in both light and dark against this page's surfaces)
-- see the skill's `scripts/validate_palette.js`. That ordering is fixed
and must not be reshuffled; the skill's rule is "assign categorical hues
in fixed order, never cycled."
"""

import html
from typing import Any, Optional

from verityai.ontology.models import ReasoningTrace, VerificationStatus
from verityai.symbolic.debugger import SymbolicDebugger

_STATUS_LABELS: dict[VerificationStatus, tuple[str, str]] = {
    VerificationStatus.PASS: ("Verified", "good"),
    VerificationStatus.FAIL: ("Failed", "critical"),
    VerificationStatus.UNKNOWN: ("Unknown", "warning"),
    VerificationStatus.TIMEOUT: ("Timeout", "warning"),
    VerificationStatus.NOT_VERIFIED: ("Not verified", "serious"),
}

_RETRIEVAL_METHOD_LABELS = {
    "lexical": "Lexical (BM25)",
    "semantic": "Semantic (embedding)",
    "hybrid": "Hybrid (fused)",
}

_FACTOR_ORDER = ["verification", "pattern_similarity", "complexity", "test_coverage"]
_FACTOR_LABELS = {
    "verification": "Verification",
    "pattern_similarity": "Pattern similarity",
    "complexity": "Complexity",
    "test_coverage": "Test coverage",
}
_FACTOR_COLOR_VARS = {
    "verification": "--series-aqua",
    "pattern_similarity": "--series-blue",
    "complexity": "--series-yellow",
    "test_coverage": "--series-magenta",
}


def render_run_view(traces: list[ReasoningTrace]) -> str:
    """Render the full reasoning-trace view for one request as self-contained HTML.

    Args:
        traces: All attempts for one request_id, in attempt order (non-empty)

    Returns:
        A complete, self-contained HTML document (no external resources)
    """
    last = traces[-1]
    status_label, status_class = _trace_status(last)
    confidence_pct = round(last.confidence_score * 100, 1)
    title = html.escape(str(last.request_id) if last.request_id else "run")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VerityAI Run {title}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="wrap">
  <h1>Reasoning Trace</h1>
  <p class="muted">{html.escape(last.user_prompt)}</p>

  <div class="card">
    <span class="pill pill-{status_class}">{status_label}</span>
    <div class="meter-label">Confidence: {confidence_pct}%</div>
    <div class="meter-track">
      <div class="meter-fill" style="width:{confidence_pct}%; background: var(--{status_class});"></div>
    </div>
  </div>

  <h2>Pipeline</h2>
  <div class="card">{_render_stepper(traces)}</div>

  <h2>Knowledge Graph Retrieval</h2>
  <div class="card">{_render_retrieval(last)}</div>

  <h2>Attempts</h2>
  {_render_attempts(traces)}

  <h2>Symbolic Verification</h2>
  <div class="card">{_render_z3_panel(last)}</div>

  <h2>Confidence Breakdown</h2>
  <div class="card">{_render_confidence_breakdown(last)}</div>
</div>
</body>
</html>
"""


def _trace_status(trace: ReasoningTrace) -> tuple[str, str]:
    if trace.verification_result is None:
        return "Failed", "critical"
    return _STATUS_LABELS.get(trace.verification_result.status, ("Unknown", "warning"))


def _render_stepper(traces: list[ReasoningTrace]) -> str:
    steps = ['<div class="step">Prompt</div>']

    retrieval = (traces[0].kg_context or {}).get("retrieval")
    if isinstance(retrieval, dict):
        mode = html.escape(str(retrieval.get("mode", "unknown")))
        steps.append(f'<div class="step">Retrieval ({mode})</div>')
    else:
        steps.append('<div class="step">Retrieval (legacy)</div>')

    for t in traces:
        seconds = f"{t.generation_seconds:.1f}s" if t.generation_seconds is not None else "?"
        label, css_class = _trace_status(t)
        retry_class = " retry" if t.attempt_number > 1 else ""
        steps.append(
            f'<div class="step{retry_class}">Attempt {t.attempt_number} ({seconds})<br>'
            f'<span class="pill pill-{css_class}">{label}</span></div>'
        )

    verdict_label, verdict_class = _trace_status(traces[-1])
    steps.append(
        f'<div class="step">Verdict<br><span class="pill pill-{verdict_class}">{verdict_label}</span></div>'
    )

    return '<div class="stepper">' + '<span class="arrow">&rarr;</span>'.join(steps) + "</div>"


def _render_retrieval(trace: ReasoningTrace) -> str:
    kg_context = trace.kg_context or {}
    if not kg_context:
        return '<p class="muted">No KG context for this run (no kg_client configured, or the fetch failed).</p>'

    rules = kg_context.get("rules") or []
    retrieval = kg_context.get("retrieval")
    parts = []

    if isinstance(retrieval, dict):
        mode = html.escape(str(retrieval.get("mode", "unknown")))
        parts.append(
            f"<p><strong>Strategy:</strong> hybrid &middot; <strong>Mode:</strong> {mode}</p>"
        )
        degraded_reason = retrieval.get("degraded_reason")
        if degraded_reason:
            parts.append(
                f'<p class="muted">Degraded to lexical-only: {html.escape(str(degraded_reason))}</p>'
            )
    else:
        parts.append("<p><strong>Strategy:</strong> legacy (fetch-all by category)</p>")

    if not rules:
        parts.append('<p class="muted">No rules retrieved.</p>')
        return "".join(parts)

    has_provenance = isinstance(rules[0], dict) and "provenance" in rules[0]
    rows = []
    if has_provenance:
        rows.append(
            "<tr><th>Rule</th><th>Method</th><th>Lexical rank</th>"
            "<th>Semantic rank</th><th>Fused score</th></tr>"
        )
        for rule in rules:
            provenance: dict[str, Any] = rule.get("provenance") or {}
            method_key = str(provenance.get("method", ""))
            method_label = _RETRIEVAL_METHOD_LABELS.get(method_key, method_key or "?")
            lexical_rank = provenance.get("lexical_rank")
            semantic_rank = provenance.get("semantic_rank")
            fused_score = provenance.get("fused_score") or 0.0
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(rule.get('name', '')))}</td>"
                f'<td><span class="badge badge-{html.escape(method_key)}">{html.escape(method_label)}</span></td>'
                f"<td>{lexical_rank if lexical_rank is not None else '&mdash;'}</td>"
                f"<td>{semantic_rank if semantic_rank is not None else '&mdash;'}</td>"
                f"<td>{fused_score:.3f}</td>"
                "</tr>"
            )
    else:
        rows.append("<tr><th>Rule</th><th>Description</th></tr>")
        for rule in rules:
            rows.append(
                f"<tr><td>{html.escape(str(rule.get('name', '')))}</td>"
                f"<td>{html.escape(str(rule.get('description', '')))}</td></tr>"
            )

    parts.append(f"<table>{''.join(rows)}</table>")
    return "".join(parts)


def _render_attempts(traces: list[ReasoningTrace]) -> str:
    """Render one card per attempt.

    `trace.failure_reason` is the *previous* attempt's failure, injected
    into THIS attempt's prompt as retry context (see agent/state.py's
    record_attempt) -- it is not this attempt's own outcome. Labeling it
    "Retry context" (and only showing it on attempt 2+) avoids the
    misleading appearance of a passing attempt still showing a failure box.
    """
    cards = []
    for t in traces:
        label, css_class = _trace_status(t)
        seconds = f"{t.generation_seconds:.2f}s" if t.generation_seconds is not None else "n/a"
        retry_context = (
            f'<p class="muted">Retry context (previous failure): {html.escape(t.failure_reason)}</p>'
            if t.failure_reason and t.attempt_number > 1
            else ""
        )
        cards.append(
            '<div class="card">'
            '<div class="row">'
            f"<strong>Attempt {t.attempt_number}</strong>"
            f'<span class="pill pill-{css_class}">{label}</span>'
            f'<span class="muted">{seconds} &middot; confidence {t.confidence_score:.0%}</span>'
            "</div>"
            f"{retry_context}"
            f"<pre>{html.escape(t.generated_code)}</pre>"
            "</div>"
        )
    return "".join(cards)


def _render_z3_panel(trace: ReasoningTrace) -> str:
    if trace.verification_result is None:
        return '<p class="muted">No verification result recorded for this attempt.</p>'

    try:
        debug_info = SymbolicDebugger(trace.generated_code).debug(trace.verification_result)
    except Exception as e:
        return (
            f'<p class="error">Could not recompute symbolic debug info: {html.escape(str(e))}</p>'
        )

    parts = [
        f"<p><strong>Status:</strong> {html.escape(str(debug_info['status']))} &middot; "
        f"<strong>Solver confidence:</strong> {debug_info['confidence']:.0%}</p>"
    ]

    violations = debug_info.get("violations") or []
    if not violations:
        parts.append('<p class="muted">No counterexamples.</p>')
        return "".join(parts)

    for violation in violations:
        parts.append(
            '<div class="card">'
            f"<p><strong>{html.escape(str(violation.get('rule') or 'Violation'))}:</strong> "
            f"{html.escape(str(violation.get('description', '')))}</p>"
            f'<p class="muted">Counterexample: {html.escape(str(violation.get("counterexample_inputs", {})))}</p>'
        )
        source_code = violation.get("source_code")
        if source_code:
            parts.append(f"<pre>{html.escape(str(source_code))}</pre>")
        fix_suggestions = violation.get("fix_suggestions") or []
        if fix_suggestions:
            items = "".join(f"<li>{html.escape(str(fix))}</li>" for fix in fix_suggestions)
            parts.append(f"<ul>{items}</ul>")
        parts.append("</div>")

    return "".join(parts)


def _render_confidence_breakdown(trace: ReasoningTrace) -> str:
    factors: Optional[dict[str, Any]] = trace.confidence_factors
    if not factors:
        return '<p class="muted">No factor breakdown recorded for this attempt.</p>'

    components: dict[str, Any] = factors.get("components", {})
    weights: dict[str, Any] = factors.get("weights", {})

    segments = []
    legend = []
    for key in _FACTOR_ORDER:
        component = float(components.get(key, 0.0))
        weight = float(weights.get(key, 0.0))
        contribution_pct = round(component * weight * 100, 1)
        color_var = _FACTOR_COLOR_VARS[key]
        label = _FACTOR_LABELS[key]

        if contribution_pct > 0:
            segments.append(
                f'<div class="bar-segment" style="width:{contribution_pct}%; background: var({color_var});" '
                f'title="{label}: {component:.0%} of factor &times; {weight:.0%} weight"></div>'
            )
        legend.append(
            f'<div class="legend-item"><span class="swatch" style="background: var({color_var});"></span>'
            f"{label}: {component:.0%} (weight {weight:.0%})</div>"
        )

    total_pct = round(float(factors.get("total", 0.0)) * 100, 1)
    return (
        f'<div class="meter-label">Total: {total_pct}%</div>'
        f'<div class="bar-track">{"".join(segments)}</div>'
        f'<div class="legend">{"".join(legend)}</div>'
    )


_CSS = """
:root {
  --surface-1: #fcfcfb;
  --page-plane: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --gridline: #e1e0d9;
  --good: #0ca30c;
  --warning: #fab219;
  --serious: #ec835a;
  --critical: #d03b3b;
  --series-aqua: #1baf7a;
  --series-blue: #2a78d6;
  --series-yellow: #eda100;
  --series-magenta: #e87ba4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface-1: #1a1a19;
    --page-plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --gridline: #2c2c2a;
    --good: #0ca30c;
    --warning: #fab219;
    --serious: #ec835a;
    --critical: #d03b3b;
    --series-aqua: #199e70;
    --series-blue: #3987e5;
    --series-yellow: #c98500;
    --series-magenta: #d55181;
  }
}
* { box-sizing: border-box; }
body {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page-plane);
  color: var(--text-primary);
  margin: 0;
  padding: 24px;
}
.wrap { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
h2 { font-size: 1.05rem; color: var(--text-secondary); margin-top: 32px; }
.card {
  background: var(--surface-1);
  border: 1px solid var(--gridline);
  border-radius: 8px;
  padding: 16px;
  margin-top: 12px;
}
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.muted { color: var(--text-muted); font-size: 0.85rem; }
.error { color: var(--critical); font-size: 0.85rem; }
pre {
  background: var(--page-plane);
  border: 1px solid var(--gridline);
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  font-size: 0.85rem;
  white-space: pre-wrap;
}
table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.85rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--gridline); }
th { color: var(--text-muted); font-weight: 600; }
.pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  color: #fff;
}
.pill-good { background: var(--good); }
.pill-warning { background: var(--warning); color: #1a1a19; }
.pill-serious { background: var(--serious); }
.pill-critical { background: var(--critical); }
.badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 6px;
  font-size: 0.7rem;
  border: 1px solid var(--gridline);
  color: var(--text-secondary);
}
.badge-hybrid { border-color: var(--series-blue); color: var(--series-blue); }
.badge-semantic { border-color: var(--series-aqua); color: var(--series-aqua); }
.badge-lexical { border-color: var(--text-muted); }
.meter-track {
  width: 100%;
  height: 14px;
  border-radius: 7px;
  background: var(--gridline);
  overflow: hidden;
  margin-top: 4px;
}
.meter-fill { height: 100%; border-radius: 7px 0 0 7px; }
.meter-label { font-size: 0.85rem; color: var(--text-secondary); margin-top: 4px; }
.bar-track {
  display: flex;
  width: 100%;
  height: 18px;
  border-radius: 4px;
  background: var(--gridline);
  overflow: hidden;
  margin-top: 6px;
  gap: 2px;
}
.bar-segment { height: 100%; }
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 10px;
  font-size: 0.8rem;
  color: var(--text-secondary);
}
.legend-item { display: flex; align-items: center; gap: 6px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.stepper { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.step {
  border: 1px solid var(--gridline);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.8rem;
  text-align: center;
}
.step.retry { border-style: dashed; }
.arrow { color: var(--text-muted); }
"""
