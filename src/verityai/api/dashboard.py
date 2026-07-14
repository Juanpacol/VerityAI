"""Self-contained HTML/JS web dashboard (Phase 4 Part C).

Served via GET /dashboard (see rest.py). No build step, no CDN
dependencies -- plain HTML/CSS/JS that calls this same API's own JSON
endpoints (/trace/{id}, /kg/algorithms, /kg/rules) via relative-path
fetch(), so it works wherever the API itself is reachable.

Confidence meter follows the dataviz skill's meter spec: fill carries
severity (good/warning/serious/critical, from the project's validated
status palette), unfilled track is a lighter step of the same ramp.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VerityAI Dashboard</title>
<style>
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
.wrap { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 4px; }
h2 { font-size: 1.05rem; color: var(--text-secondary); margin-top: 32px; }
.card {
  background: var(--surface-1);
  border: 1px solid var(--gridline);
  border-radius: 8px;
  padding: 16px;
  margin-top: 12px;
}
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
input, select, button {
  font: inherit;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--gridline);
  background: var(--surface-1);
  color: var(--text-primary);
}
button { cursor: pointer; }
button:hover { background: var(--gridline); }
pre {
  background: var(--page-plane);
  border: 1px solid var(--gridline);
  border-radius: 6px;
  padding: 12px;
  overflow-x: auto;
  font-size: 0.85rem;
}
table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.85rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--gridline); }
th { color: var(--text-muted); font-weight: 600; }
.meter-track {
  width: 100%;
  height: 14px;
  border-radius: 7px;
  background: var(--gridline);
  overflow: hidden;
}
.meter-fill { height: 100%; border-radius: 7px 0 0 7px; transition: width 0.2s ease; }
.meter-label { font-size: 0.85rem; color: var(--text-secondary); margin-top: 4px; }
.error { color: var(--critical); font-size: 0.85rem; }
.muted { color: var(--text-muted); font-size: 0.85rem; }
</style>
</head>
<body>
<div class="wrap">
  <h1>VerityAI Dashboard</h1>
  <p class="muted">Code + reasoning trace viewer, confidence meter, and KG explorer.</p>

  <h2>Trace Viewer</h2>
  <div class="card">
    <div class="row">
      <input id="trace-id-input" type="text" placeholder="Trace ID (UUID)" size="40">
      <button onclick="loadTrace()">Load</button>
    </div>
    <div id="trace-error" class="error"></div>
    <div id="trace-result" style="display:none; margin-top: 12px;">
      <div class="meter-label" id="confidence-label"></div>
      <div class="meter-track"><div id="confidence-fill" class="meter-fill"></div></div>
      <p><strong>Status:</strong> <span id="trace-status"></span></p>
      <p><strong>Prompt:</strong> <span id="trace-prompt"></span></p>
      <p><strong>Reasoning:</strong></p>
      <pre id="trace-reasoning"></pre>
      <p><strong>Generated Code:</strong></p>
      <pre id="trace-code"></pre>
      <p><a id="trace-run-link" href="#" style="display:none;">View full reasoning trace &rarr;</a></p>
    </div>
  </div>

  <h2>Knowledge Graph Explorer</h2>
  <div class="card">
    <div class="row">
      <select id="kg-language">
        <option value="python">python</option>
        <option value="java">java</option>
        <option value="cpp">cpp</option>
      </select>
      <button onclick="loadKG()">Refresh</button>
    </div>
    <div id="kg-error" class="error"></div>
    <h3 style="margin-bottom:4px;">Algorithms</h3>
    <table id="algorithms-table"><thead><tr><th>Name</th><th>Complexity (time/space)</th></tr></thead><tbody></tbody></table>
    <h3 style="margin-bottom:4px;">Rules</h3>
    <table id="rules-table"><thead><tr><th>Name</th><th>Category</th><th>Severity</th></tr></thead><tbody></tbody></table>
  </div>
</div>

<script>
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function confidenceColor(confidence) {
  if (confidence >= 0.8) return "var(--good)";
  if (confidence >= 0.5) return "var(--warning)";
  if (confidence >= 0.2) return "var(--serious)";
  return "var(--critical)";
}

async function loadTrace() {
  const id = document.getElementById("trace-id-input").value.trim();
  const errorEl = document.getElementById("trace-error");
  const resultEl = document.getElementById("trace-result");
  errorEl.textContent = "";
  resultEl.style.display = "none";

  if (!id) {
    errorEl.textContent = "Enter a trace ID.";
    return;
  }

  try {
    const response = await fetch(`/trace/${encodeURIComponent(id)}`);
    if (!response.ok) {
      errorEl.textContent = response.status === 404 ? "Trace not found." : `Error: ${response.status}`;
      return;
    }
    const trace = await response.json();
    const confidence = trace.confidence_score || 0;
    const status = trace.verification_result ? trace.verification_result.status : "unknown";

    document.getElementById("confidence-label").textContent = `Confidence: ${(confidence * 100).toFixed(1)}%`;
    const fill = document.getElementById("confidence-fill");
    fill.style.width = `${(confidence * 100).toFixed(1)}%`;
    fill.style.background = confidenceColor(confidence);

    document.getElementById("trace-status").textContent = status;
    document.getElementById("trace-prompt").textContent = trace.user_prompt;
    document.getElementById("trace-reasoning").textContent = trace.llm_reasoning || "(none)";
    document.getElementById("trace-code").textContent = trace.generated_code;

    const runLink = document.getElementById("trace-run-link");
    if (trace.request_id) {
      runLink.href = `/runs/${encodeURIComponent(trace.request_id)}/view`;
      runLink.style.display = "inline";
    } else {
      runLink.style.display = "none";
    }

    resultEl.style.display = "block";
  } catch (e) {
    errorEl.textContent = `Request failed: ${e}`;
  }
}

async function loadKG() {
  const language = document.getElementById("kg-language").value;
  const errorEl = document.getElementById("kg-error");
  errorEl.textContent = "";

  try {
    const [algosResponse, rulesResponse] = await Promise.all([
      fetch(`/kg/algorithms?language=${encodeURIComponent(language)}`),
      fetch(`/kg/rules?language=${encodeURIComponent(language)}`),
    ]);
    if (!algosResponse.ok || !rulesResponse.ok) {
      errorEl.textContent = "Could not reach the Knowledge Graph.";
      return;
    }
    const algorithms = await algosResponse.json();
    const rules = await rulesResponse.json();

    const algoBody = document.querySelector("#algorithms-table tbody");
    algoBody.innerHTML = algorithms.map(a =>
      `<tr><td>${escapeHtml(a.name)}</td><td>${escapeHtml(a.complexity_time)} / ${escapeHtml(a.complexity_space)}</td></tr>`
    ).join("") || '<tr><td colspan="2" class="muted">No algorithms found.</td></tr>';

    const rulesBody = document.querySelector("#rules-table tbody");
    rulesBody.innerHTML = rules.map(r =>
      `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.category)}</td><td>${escapeHtml(r.severity)}</td></tr>`
    ).join("") || '<tr><td colspan="3" class="muted">No rules found.</td></tr>';
  } catch (e) {
    errorEl.textContent = `Request failed: ${e}`;
  }
}

loadKG();
</script>
</body>
</html>
"""


def render_dashboard() -> str:
    return DASHBOARD_HTML
