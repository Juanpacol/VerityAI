"""Self-contained HTML dashboard comparing the 3 baseline configurations.

Renders whatever BenchmarkOutcome data it's given -- same caveat as
report.py: it has no opinion on whether that data is from a live Ollama
run or a scripted test double. Callers MUST pass a `data_source_note`
describing where the numbers came from, since a dashboard with no such
caption invites readers to mistake illustrative data for a real result.

Palette: categorical slots 1/2/3 (blue/aqua/yellow) from the project's
validated default palette (see dataviz skill's references/palette.md),
assigned in fixed order to raw_llm / single_shot_z3 / verityai_full.
Aqua and yellow fall below 3:1 contrast on the light surface (validated
via scripts/validate_palette.js), so the "relief rule" applies: this
dashboard always ships direct labels on the latency chart and a full
data table, never relying on the grouped-bar-chart colors alone.
"""

import html

from verityai.evaluation.metrics import (
    BenchmarkOutcome,
    compute_classification_metrics,
    confidence_distribution,
    latency_distribution,
)

BASELINE_ORDER = ["raw_llm", "single_shot_z3", "verityai_full"]
BASELINE_DISPLAY_NAMES = {
    "raw_llm": "Raw LLM (no verification)",
    "single_shot_z3": "LLM + Z3 (no retry)",
    "verityai_full": "VerityAI (full retry loop)",
}
# (light, dark) hex per baseline, categorical slots 1/2/3 -- validated via
# scripts/validate_palette.js "#2a78d6,#1baf7a,#eda100" --mode light
# and "#3987e5,#199e70,#c98500" --mode dark.
BASELINE_COLORS = {
    "raw_llm": ("#2a78d6", "#3987e5"),
    "single_shot_z3": ("#1baf7a", "#199e70"),
    "verityai_full": ("#eda100", "#c98500"),
}

METRIC_LABELS = [
    ("accuracy", "Accuracy"),
    ("precision", "Precision"),
    ("recall", "Recall"),
    ("f1", "F1"),
]

_CHART_W = 680
_CHART_H = 260
_MARGIN = {"top": 20, "right": 20, "bottom": 36, "left": 36}
_BAR_W = 20
_BAR_GAP = 2


def _rounded_top_bar_path(x: float, w: float, y_top: float, y_base: float, r: float = 4) -> str:
    """SVG path for a bar with rounded top corners, square at the baseline."""
    if y_base - y_top < r:
        r = max(0.0, y_base - y_top)
    return (
        f"M{x},{y_base} L{x},{y_top + r} Q{x},{y_top} {x + r},{y_top} "
        f"L{x + w - r},{y_top} Q{x + w},{y_top} {x + w},{y_top + r} "
        f"L{x + w},{y_base} Z"
    )


def _present_baselines(results: dict[str, list[BenchmarkOutcome]]) -> list[str]:
    return [name for name in BASELINE_ORDER if name in results]


def _render_legend(baselines: list[str]) -> str:
    items = []
    for name in baselines:
        display = html.escape(BASELINE_DISPLAY_NAMES.get(name, name))
        items.append(
            f'<span class="legend-item"><span class="swatch swatch-{name}"></span>{display}</span>'
        )
    return '<div class="legend">' + "".join(items) + "</div>"


def _render_metrics_chart(results: dict[str, list[BenchmarkOutcome]], baselines: list[str]) -> str:
    plot_w = _CHART_W - _MARGIN["left"] - _MARGIN["right"]
    plot_h = _CHART_H - _MARGIN["top"] - _MARGIN["bottom"]
    baseline_y = _MARGIN["top"] + plot_h
    n_groups = len(METRIC_LABELS)
    group_slot_w = plot_w / n_groups
    n_bars = len(baselines)
    bars_total_w = n_bars * _BAR_W + max(0, n_bars - 1) * _BAR_GAP
    start_offset = (group_slot_w - bars_total_w) / 2

    svg_parts = [
        f'<svg viewBox="0 0 {_CHART_W} {_CHART_H}" role="img" '
        f'aria-label="Accuracy, precision, recall and F1 per baseline">'
    ]

    # Recessive gridlines at 0/25/50/75/100%.
    for pct in (0, 25, 50, 75, 100):
        y = _MARGIN["top"] + plot_h * (1 - pct / 100)
        svg_parts.append(
            f'<line x1="{_MARGIN["left"]}" y1="{y:.1f}" '
            f'x2="{_CHART_W - _MARGIN["right"]}" y2="{y:.1f}" class="gridline" />'
        )
        svg_parts.append(
            f'<text x="{_MARGIN["left"] - 6}" y="{y + 3:.1f}" class="axis-label" '
            f'text-anchor="end">{pct}%</text>'
        )

    metrics_by_baseline = {name: compute_classification_metrics(results[name]) for name in baselines}

    for gi, (metric_key, metric_label) in enumerate(METRIC_LABELS):
        group_x = _MARGIN["left"] + gi * group_slot_w
        for bi, name in enumerate(baselines):
            value = metrics_by_baseline[name][metric_key]
            bar_x = group_x + start_offset + bi * (_BAR_W + _BAR_GAP)
            bar_height = value * plot_h
            bar_top = baseline_y - bar_height
            path = _rounded_top_bar_path(bar_x, _BAR_W, bar_top, baseline_y)
            display = html.escape(BASELINE_DISPLAY_NAMES.get(name, name))
            svg_parts.append(
                f'<path d="{path}" class="bar bar-{name}">'
                f"<title>{display} — {metric_label}: {value:.1%}</title></path>"
            )
        label_x = group_x + group_slot_w / 2
        svg_parts.append(
            f'<text x="{label_x:.1f}" y="{_CHART_H - 8}" class="axis-label" '
            f'text-anchor="middle">{metric_label}</text>'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _render_latency_chart(results: dict[str, list[BenchmarkOutcome]], baselines: list[str]) -> str:
    plot_w = _CHART_W - _MARGIN["left"] - _MARGIN["right"]
    plot_h = _CHART_H - _MARGIN["top"] - _MARGIN["bottom"]
    baseline_y = _MARGIN["top"] + plot_h

    latencies = {name: latency_distribution(results[name])["mean"] for name in baselines}
    max_latency = max(latencies.values(), default=0.0) or 1.0

    n_bars = len(baselines)
    group_slot_w = plot_w / max(1, n_bars)

    svg_parts = [
        f'<svg viewBox="0 0 {_CHART_W} {_CHART_H}" role="img" '
        f'aria-label="Average latency in seconds per baseline">'
    ]
    svg_parts.append(
        f'<line x1="{_MARGIN["left"]}" y1="{baseline_y:.1f}" '
        f'x2="{_CHART_W - _MARGIN["right"]}" y2="{baseline_y:.1f}" class="axis-baseline" />'
    )

    for i, name in enumerate(baselines):
        value = latencies[name]
        bar_x = _MARGIN["left"] + i * group_slot_w + (group_slot_w - _BAR_W) / 2
        bar_height = (value / max_latency) * plot_h
        bar_top = baseline_y - bar_height
        display = html.escape(BASELINE_DISPLAY_NAMES.get(name, name))
        path = _rounded_top_bar_path(bar_x, _BAR_W, bar_top, baseline_y)
        svg_parts.append(
            f'<path d="{path}" class="bar bar-{name}">'
            f"<title>{display}: {value:.3f}s</title></path>"
        )
        # Direct label at the bar tip -- only 3 bars here, so labeling every
        # one stays legible (unlike the 12-bar metrics chart above).
        svg_parts.append(
            f'<text x="{bar_x + _BAR_W / 2:.1f}" y="{bar_top - 6:.1f}" '
            f'class="bar-label" text-anchor="middle">{value:.3f}s</text>'
        )
        svg_parts.append(
            f'<text x="{bar_x + _BAR_W / 2:.1f}" y="{_CHART_H - 8}" class="axis-label" '
            f'text-anchor="middle">{display}</text>'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _render_table(results: dict[str, list[BenchmarkOutcome]], baselines: list[str]) -> str:
    rows = []
    for name in baselines:
        metrics = compute_classification_metrics(results[name])
        confidence = confidence_distribution(results[name])
        latency = latency_distribution(results[name])
        display = html.escape(BASELINE_DISPLAY_NAMES.get(name, name))
        rows.append(
            "<tr>"
            f"<td>{display}</td>"
            f"<td>{metrics['accuracy']:.1%}</td>"
            f"<td>{metrics['precision']:.1%}</td>"
            f"<td>{metrics['recall']:.1%}</td>"
            f"<td>{metrics['f1']:.1%}</td>"
            f"<td>{metrics['abstention_rate']:.1%}</td>"
            f"<td>{metrics['novel_rate']:.1%}</td>"
            f"<td>{confidence['mean']:.2f}</td>"
            f"<td>{latency['mean']:.3f}</td>"
            "</tr>"
        )

    return (
        '<table class="data-table"><thead><tr>'
        "<th>Baseline</th><th>Accuracy</th><th>Precision</th><th>Recall</th>"
        "<th>F1</th><th>Abstention</th><th>Novel</th><th>Avg Confidence</th>"
        "<th>Avg Latency (s)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


_STYLE = """
.viz-root {
  --surface-1: #fcfcfb;
  --page-plane: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #898781;
  --gridline: #e1e0d9;
  --axis-baseline: #c3c2b7;
  --bar-raw_llm: #2a78d6;
  --bar-single_shot_z3: #1baf7a;
  --bar-verityai_full: #eda100;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page-plane);
  color: var(--text-primary);
  padding: 24px;
  max-width: 760px;
  margin: 0 auto;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --surface-1: #1a1a19;
    --page-plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --gridline: #2c2c2a;
    --axis-baseline: #383835;
    --bar-raw_llm: #3987e5;
    --bar-single_shot_z3: #199e70;
    --bar-verityai_full: #c98500;
  }
}
.viz-root .bar-raw_llm { fill: var(--bar-raw_llm); }
.viz-root .bar-single_shot_z3 { fill: var(--bar-single_shot_z3); }
.viz-root .bar-verityai_full { fill: var(--bar-verityai_full); }
.viz-root .swatch-raw_llm { background: var(--bar-raw_llm); }
.viz-root .swatch-single_shot_z3 { background: var(--bar-single_shot_z3); }
.viz-root .swatch-verityai_full { background: var(--bar-verityai_full); }
.viz-root h1 { font-size: 1.4rem; margin-bottom: 4px; }
.viz-root h2 { font-size: 1rem; color: var(--text-secondary); margin-top: 28px; }
.viz-root .note {
  color: var(--text-secondary);
  font-size: 0.9rem;
  padding: 10px 12px;
  background: var(--surface-1);
  border-radius: 6px;
  border: 1px solid var(--gridline);
}
.viz-root .legend { display: flex; gap: 16px; margin: 12px 0; flex-wrap: wrap; }
.viz-root .legend-item {
  display: flex; align-items: center; gap: 6px;
  color: var(--text-secondary); font-size: 0.85rem;
}
.viz-root .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.viz-root svg { width: 100%; height: auto; background: var(--surface-1); border-radius: 8px; }
.viz-root .gridline { stroke: var(--gridline); stroke-width: 1; }
.viz-root .axis-baseline { stroke: var(--axis-baseline); stroke-width: 1; }
.viz-root .axis-label { fill: var(--text-muted); font-size: 10px; }
.viz-root .bar-label { fill: var(--text-secondary); font-size: 11px; font-weight: 600; }
.viz-root .data-table {
  width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.85rem;
  overflow-x: auto; display: block;
}
.viz-root .data-table th, .viz-root .data-table td {
  text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--gridline);
  font-variant-numeric: tabular-nums;
}
.viz-root .data-table th:first-child, .viz-root .data-table td:first-child {
  text-align: left; font-variant-numeric: normal;
}
.viz-root .data-table th { color: var(--text-muted); font-weight: 600; }
"""


def render_html_dashboard(
    results: dict[str, list[BenchmarkOutcome]],
    data_source_note: str,
) -> str:
    """Render a self-contained HTML dashboard comparing the given baselines.

    Args:
        results: Output of evaluation.baselines.run_all_baselines (or a
            subset -- any keys from BASELINE_ORDER present in `results`
            are rendered; missing ones are simply skipped).
        data_source_note: Required, shown as a caption banner -- e.g.
            "Simulated FakeLLMClient data for framework validation, not
            real llama2:13b results." A dashboard with no such caption
            invites readers to mistake illustrative numbers for a real
            benchmark run.
    """
    baselines = _present_baselines(results)

    return f"""<div class="viz-root">
<style>{_STYLE}</style>
<h1>VerityAI Baseline Comparison</h1>
<p class="note">{html.escape(data_source_note)}</p>
{_render_legend(baselines)}
<h2>Accuracy / Precision / Recall / F1</h2>
{_render_metrics_chart(results, baselines)}
<h2>Average Latency</h2>
{_render_latency_chart(results, baselines)}
<h2>Data</h2>
{_render_table(results, baselines)}
</div>"""
