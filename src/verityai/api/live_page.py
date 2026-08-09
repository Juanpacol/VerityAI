"""The live pipeline view served at GET /live.

Submit a prompt, watch the generate-verify-retry loop happen step by step,
then answer the T5 study questions. Self-contained HTML+CSS+JS with no
build step and no CDN, matching api/dashboard.py -- and reusing
run_view.live_css() so the streamed panels look exactly like the post-hoc
view at /runs/{id}/view.

Two things this page deliberately does NOT do:

- It does not decide what the participant sees. Panel visibility is the T5
  manipulation, and it is enforced server-side (api/live_fragments.py):
  events for a suppressed panel arrive with no HTML and no underlying
  numbers. This page just renders whatever the stream sends it. A
  CSS-based version would be defeated by opening dev tools.
- It does not narrate. Every sentence in the log comes from the event's
  `message` field, produced by deterministic templates in
  agent/event_narration.py.
"""

from verityai.api.run_view import live_css

_EXTRA_CSS = """
.consent-card { border-left: 3px solid var(--series-blue); }
.consent-card ul { margin: 8px 0 12px; padding-left: 20px; color: var(--text-secondary); }
.consent-card li { margin: 4px 0; font-size: 0.9rem; }
label.check { display: flex; gap: 8px; align-items: flex-start; cursor: pointer; }
textarea, select, input[type=text] {
  width: 100%; font: inherit; padding: 8px 10px; border-radius: 6px;
  border: 1px solid var(--gridline); background: var(--page-plane);
  color: var(--text-primary);
}
textarea { min-height: 84px; resize: vertical; }
button {
  font: inherit; font-weight: 600; padding: 9px 18px; border-radius: 6px;
  border: 1px solid transparent; background: var(--series-blue); color: #fff;
  cursor: pointer;
}
button:disabled { opacity: 0.45; cursor: not-allowed; }
fieldset { border: 1px solid var(--gridline); border-radius: 6px; margin: 14px 0; padding: 12px 14px; }
legend { font-weight: 600; font-size: 0.9rem; padding: 0 6px; }
fieldset .hint { color: var(--text-muted); font-size: 0.82rem; margin: 0 0 10px; }
.radio-row { display: flex; gap: 8px; align-items: center; margin: 6px 0; font-size: 0.92rem; }

/* Pipeline stepper: chips advance pending -> active -> done. */
.steps { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.step {
  font-size: 0.8rem; padding: 4px 10px; border-radius: 999px;
  border: 1px solid var(--gridline); color: var(--text-muted);
  background: var(--page-plane);
}
.step.active {
  color: var(--text-primary); border-color: var(--series-blue);
  animation: pulse 1.2s ease-in-out infinite;
}
.step.done { color: var(--good); border-color: var(--good); }
.step.failed { color: var(--critical); border-color: var(--critical); }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.45; } }
@media (prefers-reduced-motion: reduce) { .step.active { animation: none; } }

/* Narration log: the "watch it think" surface. */
#log { max-height: 320px; overflow-y: auto; }
.log-line {
  display: flex; gap: 10px; padding: 5px 0;
  border-top: 1px solid var(--gridline); font-size: 0.9rem;
}
.log-line:first-child { border-top: none; }
.log-time { color: var(--text-muted); font-variant-numeric: tabular-nums; flex: 0 0 52px; }
#elapsed { font-variant-numeric: tabular-nums; color: var(--text-secondary); }
.hidden { display: none; }
.panel-empty { color: var(--text-muted); font-size: 0.85rem; }
footer { margin-top: 40px; font-size: 0.82rem; color: var(--text-muted); }
footer p { margin: 6px 0; }
"""

_HTML = """<div class="wrap">
<h1>VerityAI &mdash; watch the verification happen</h1>
<p class="muted">Ask for a piece of code. This page shows each step the
system takes to decide whether it can prove the result correct.</p>

<section class="card consent-card" id="consent-card">
  <h2 style="margin-top:0">Before you start</h2>
  <p class="muted">This page is part of a small research study on what
  actually makes developers trust AI-generated code.</p>
  <ul>
    <li>What is recorded: the prompt you type, the code the system
        produces, and your answers to the questions at the end.</li>
    <li>What is not recorded: your name, email, or account. There is no
        login and no tracking beyond your own run.</li>
    <li>This is a solo research project, not a company, and there is no
        payment for taking part.</li>
    <li>Anonymised answers may be summarised in the project's public
        write-up. Free-text answers are stored and could be quoted, so
        please don't paste anything confidential.</li>
    <li>The person who built this system is also the person running the
        study. That is a real bias risk, disclosed here rather than
        hidden &mdash; please be blunt, including about what doesn't
        work.</li>
    <li>You can stop at any time by closing the tab.</li>
  </ul>
  <label class="check">
    <input type="checkbox" id="consent"> I'm 18 or older and I agree to
    take part on these terms.
  </label>
</section>

<section class="card">
  <label for="prompt"><strong>What should the system write?</strong></label>
  <p class="muted">For example: "a function that returns the median of a
  list of integers".</p>
  <textarea id="prompt" disabled placeholder="Describe the function you want..."></textarea>
  <div class="row" style="margin-top:10px">
    <label for="attempts" class="muted">Retry budget</label>
    <select id="attempts" disabled style="width:auto">
      <option value="1">1 attempt</option>
      <option value="3" selected>up to 3 attempts</option>
      <option value="5">up to 5 attempts</option>
    </select>
    <button id="run" disabled>Run</button>
    <span id="elapsed"></span>
  </div>
  <p class="muted" id="status"></p>
</section>

<section id="progress" class="hidden">
  <h2>Pipeline</h2>
  <div class="steps" id="steps"></div>

  <h2>What the system is doing</h2>
  <div class="card" id="log"></div>

  <div id="panel-retrieval"><h2>Knowledge Graph Retrieval</h2>
    <div class="card"><p class="panel-empty">Waiting...</p></div></div>
  <div id="panel-z3"><h2>Symbolic Verification</h2>
    <div class="card"><p class="panel-empty">Waiting...</p></div></div>
  <div id="panel-confidence"><h2>Confidence Breakdown</h2>
    <div class="card"><p class="panel-empty">Waiting...</p></div></div>

  <h2>Generated code</h2>
  <div class="card" id="panel-code"><p class="panel-empty">Waiting...</p></div>
</section>

<section id="questions" class="hidden">
  <h2>A few questions</h2>
  <div class="card">
    <p class="muted">Please answer based on what you actually saw on this
    page, not on what you assume the system does underneath.</p>

    <fieldset>
      <legend>1. Do you trust this code?</legend>
      <div class="radio-row"><input type="radio" name="trust" id="trust-yes" value="yes">
        <label for="trust-yes">Yes</label></div>
      <div class="radio-row"><input type="radio" name="trust" id="trust-no" value="no">
        <label for="trust-no">No</label></div>
      <label for="trust-reason" class="muted">Why? What specifically made
      you say that?</label>
      <textarea id="trust-reason"></textarea>
    </fieldset>

    <fieldset>
      <legend>2. What would you actually do with it?</legend>
      <p class="hint">This is a separate question from the one above &mdash;
      please answer it independently, even if it feels like a repeat.</p>
      <div class="radio-row"><input type="radio" name="merge" id="merge-as-is" value="merge_as_is">
        <label for="merge-as-is">Merge it as-is</label></div>
      <div class="radio-row"><input type="radio" name="merge" id="merge-skim" value="merge_after_skim">
        <label for="merge-skim">Merge it after a quick read-through</label></div>
      <div class="radio-row"><input type="radio" name="merge" id="merge-review" value="full_review">
        <label for="merge-review">Insist on a full review before merging</label></div>
    </fieldset>

    <fieldset>
      <legend>3. If you could keep only one thing on this page, what would you keep?</legend>
      <div class="radio-row"><input type="radio" name="keep" id="keep-z3" value="z3">
        <label for="keep-z3">The symbolic verification result</label></div>
      <div class="radio-row"><input type="radio" name="keep" id="keep-confidence" value="confidence">
        <label for="keep-confidence">The confidence score and its breakdown</label></div>
      <div class="radio-row"><input type="radio" name="keep" id="keep-retrieval" value="retrieval">
        <label for="keep-retrieval">Seeing which rules were checked</label></div>
      <div class="radio-row"><input type="radio" name="keep" id="keep-code" value="code">
        <label for="keep-code">Just the code itself</label></div>
      <div class="radio-row"><input type="radio" name="keep" id="keep-other" value="other">
        <label for="keep-other">Something else:</label>
        <input type="text" id="keep-other-text" style="flex:1"></div>
    </fieldset>

    <fieldset>
      <legend>4. Anything else</legend>
      <label for="reduced" class="muted">Did anything on this page make you
      trust the code <em>less</em>, or confuse you?</label>
      <textarea id="reduced"></textarea>
      <label for="comparison" class="muted">How does this compare to how you
      decide whether to trust Copilot / ChatGPT output today?</label>
      <textarea id="comparison"></textarea>
      <label for="experience" class="muted">How often do you use AI coding
      tools? (Any answer is fine &mdash; this is context, not a filter.)</label>
      <input type="text" id="experience">
    </fieldset>

    <button id="submit">Submit answers</button>
    <p class="muted" id="submit-status"></p>
  </div>
</section>

<footer>
  <p><strong>About what this page can and cannot tell us.</strong> Because
  you chose your own prompt, no two participants judge the same code. That
  makes it weaker than a fixed set of samples for comparing people directly,
  and stronger for seeing how the system behaves on tasks people actually
  care about, with more participants and without the researcher watching
  over your shoulder. Both designs are described in the project's T5
  protocol document.</p>
  <p id="permalink-wrap" class="hidden">Full trace for this run:
  <a id="permalink" href="#">reasoning trace view</a></p>
</footer>
</div>
"""

_JS = """
(function () {
  var consent = document.getElementById('consent');
  var prompt = document.getElementById('prompt');
  var attempts = document.getElementById('attempts');
  var runBtn = document.getElementById('run');
  var statusEl = document.getElementById('status');
  var elapsedEl = document.getElementById('elapsed');
  var progress = document.getElementById('progress');
  var questions = document.getElementById('questions');
  var stepsEl = document.getElementById('steps');
  var logEl = document.getElementById('log');
  var submitBtn = document.getElementById('submit');
  var submitStatus = document.getElementById('submit-status');

  var runId = null;
  var source = null;
  var timer = null;
  var startedAt = null;
  var steps = {};

  consent.addEventListener('change', function () {
    var ok = consent.checked;
    prompt.disabled = !ok;
    attempts.disabled = !ok;
    runBtn.disabled = !ok;
  });

  function setStep(key, label, state) {
    var el = steps[key];
    if (!el) {
      el = document.createElement('span');
      el.className = 'step';
      steps[key] = el;
      stepsEl.appendChild(el);
    }
    if (label) { el.textContent = label; }
    el.className = 'step ' + (state || '');
  }

  function log(message, seconds) {
    var line = document.createElement('div');
    line.className = 'log-line';
    var time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = (seconds || 0).toFixed(1) + 's';
    var text = document.createElement('span');
    text.textContent = message;
    line.appendChild(time);
    line.appendChild(text);
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function panel(id, html) {
    var card = document.querySelector('#' + id + ' .card') || document.getElementById(id);
    if (!card) { return; }
    // Server-rendered and server-escaped; see api/run_view.py.
    card.innerHTML = html;
  }

  function tick() {
    if (!startedAt) { return; }
    elapsedEl.textContent = ((Date.now() - startedAt) / 1000).toFixed(0) + 's elapsed';
  }

  function handle(ev) {
    var d = ev.data || {};
    if (ev.message) { log(ev.message, ev.elapsed_seconds); }

    switch (ev.type) {
      case 'run_started':
        setStep('prompt', 'Prompt', 'done');
        break;
      case 'retrieval_started':
        setStep('retrieval', 'Retrieval', 'active');
        break;
      case 'retrieval_completed':
        setStep('retrieval', 'Retrieval', 'done');
        if (ev.html) { panel('panel-retrieval', ev.html); }
        break;
      case 'attempt_started':
        setStep('gen' + ev.attempt_number, 'Attempt ' + ev.attempt_number + ': generate', 'active');
        break;
      case 'generation_completed':
        setStep('gen' + ev.attempt_number, null, 'done');
        if (d.code) {
          var pre = document.createElement('pre');
          pre.textContent = d.code;
          var codePanel = document.getElementById('panel-code');
          codePanel.innerHTML = '';
          codePanel.appendChild(pre);
        }
        break;
      case 'verification_started':
        setStep('ver' + ev.attempt_number, 'Attempt ' + ev.attempt_number + ': verify', 'active');
        break;
      case 'verification_completed':
        setStep('ver' + ev.attempt_number, null, d.status === 'fail' ? 'failed' : 'done');
        if (ev.html) { panel('panel-z3', ev.html); }
        break;
      case 'confidence_computed':
        setStep('score' + ev.attempt_number, 'Attempt ' + ev.attempt_number + ': score', 'done');
        if (ev.html) { panel('panel-confidence', ev.html); }
        break;
      case 'retry_scheduled':
        setStep('retry' + d.next_attempt_number, 'Retrying', 'done');
        break;
      case 'run_completed':
        setStep('verdict', 'Verdict: ' + d.status, d.status === 'success' ? 'done' : 'failed');
        break;
      case 'run_failed':
        setStep('verdict', 'Aborted', 'failed');
        break;
    }
  }

  function finish() {
    if (source) { source.close(); source = null; }
    if (timer) { clearInterval(timer); timer = null; }
    runBtn.disabled = false;
    statusEl.textContent = 'Finished. Please answer the questions below.';
    var link = document.getElementById('permalink');
    link.href = '/runs/' + runId + '/view';
    document.getElementById('permalink-wrap').classList.remove('hidden');
    questions.classList.remove('hidden');
    questions.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  runBtn.addEventListener('click', function () {
    if (!prompt.value.trim()) { statusEl.textContent = 'Please describe what you want first.'; return; }
    runBtn.disabled = true;
    statusEl.textContent = 'Starting...';
    logEl.innerHTML = '';
    stepsEl.innerHTML = '';
    steps = {};

    fetch('/live/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: prompt.value,
        max_attempts: parseInt(attempts.value, 10),
        consent: true
      })
    }).then(function (r) {
      if (!r.ok) { return r.json().then(function (e) { throw new Error(e.detail || r.status); }); }
      return r.json();
    }).then(function (created) {
      runId = created.run_id;
      progress.classList.remove('hidden');
      statusEl.textContent = 'Running. This usually takes one to two minutes.';
      startedAt = Date.now();
      timer = setInterval(tick, 1000);

      source = new EventSource(created.stream_url);
      source.addEventListener('done', finish);
      source.onmessage = function (e) { handle(JSON.parse(e.data)); };
      // Named SSE events don't reach onmessage, so subscribe explicitly.
      ['run_started','retrieval_started','retrieval_completed','attempt_started',
       'generation_completed','verification_started','verification_completed',
       'confidence_computed','attempt_completed','retry_scheduled',
       'run_completed','run_failed'].forEach(function (name) {
        source.addEventListener(name, function (e) { handle(JSON.parse(e.data)); });
      });
      source.onerror = function () {
        statusEl.textContent = 'Connection interrupted \\u2014 reconnecting...';
      };
    }).catch(function (err) {
      runBtn.disabled = false;
      statusEl.textContent = 'Could not start: ' + err.message;
    });
  });

  function radioValue(name) {
    var checked = document.querySelector('input[name="' + name + '"]:checked');
    return checked ? checked.value : null;
  }

  submitBtn.addEventListener('click', function () {
    var trust = radioValue('trust');
    var merge = radioValue('merge');
    if (trust === null || merge === null) {
      submitStatus.textContent = 'Please answer questions 1 and 2.';
      return;
    }
    submitBtn.disabled = true;
    submitStatus.textContent = 'Sending...';

    fetch('/study/responses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        run_id: runId,
        trusts_code: trust === 'yes',
        trust_reason: document.getElementById('trust-reason').value,
        merge_intent: merge,
        kept_element: radioValue('keep'),
        kept_element_other: document.getElementById('keep-other-text').value || null,
        reduced_trust_note: document.getElementById('reduced').value || null,
        comparison_to_current_tools: document.getElementById('comparison').value || null,
        experience_with_ai_tools: document.getElementById('experience').value || null
      })
    }).then(function (r) {
      if (!r.ok) { return r.json().then(function (e) { throw new Error(e.detail || r.status); }); }
      submitStatus.textContent = 'Thank you \\u2014 your answers were recorded.';
    }).catch(function (err) {
      submitBtn.disabled = false;
      submitStatus.textContent = 'Could not submit: ' + err.message;
    });
  });
})();
"""


def render_live_page() -> str:
    """Render the live view as one self-contained HTML document."""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>VerityAI &mdash; live verification</title>"
        f"<style>{live_css()}{_EXTRA_CSS}</style></head>"
        f"<body>{_HTML}<script>{_JS}</script></body></html>"
    )
