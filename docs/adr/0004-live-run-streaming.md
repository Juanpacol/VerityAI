# ADR-0004 — Streaming the pipeline live (SSE) and running T5 through it

## Status

Accepted (live-view phase).

## Context

`POST /generate` blocks for 65-125s on a real run (measured — see
`docs/PHASE_3_METHODOLOGY.md`) and returns nothing until the whole
generate-verify-retry loop finishes. A user sees a blank screen and then a
result. That wastes the one thing this project has that a plain LLM
endpoint does not: a *process* worth watching — which rules were retrieved
from the KG, what Z3 concluded, what counterexample killed an attempt, why
a retry fired. The reasoning trace already existed; it was only ever
rendered after the fact, at `/runs/{id}/view`.

Separately, T5 (`docs/T5_HUMAN_EVAL_PROTOCOL.md`) — the one item in the
research roadmap that fundamentally needs human participants — had a
finished protocol and real materials but had never been run, because
running it meant scheduling a moderated call per participant.

## Decisions

### 1. Server-Sent Events, not WebSocket or DB polling

Traffic is strictly server-to-client. `EventSource` is native to every
browser, reconnects on its own, and needs no client library or new
dependency. WebSocket would add a bidirectional protocol nothing here uses.
Polling a database would require inventing rows for events that have no
table (retrieval finished, generation finished) and would add latency plus
request volume against the rate limiter.

### 2. A lock + deque + poll bridges the sync orchestrator to the async stream

`Orchestrator.run()` is synchronous and now runs on a daemon thread. The
emitter appends events to a bounded `deque` under a `threading.Lock` and
sets a `threading.Event`; the async SSE generator drains it every 150ms.
No `BlockingPortal`, no `run_coroutine_threadsafe`, no asyncio primitive
touched from the worker thread. 150ms of latency is invisible against
stages that take seconds, and the sync side stays completely ignorant of
the event loop — which is what keeps `agent/` free of any async concern.

### 3. Two-call handshake, so `run_id == request_id`

The client needs a stream URL *before* the run starts, but `request_id`
was generated inside `run()`. Rather than invent a second identifier space,
`Orchestrator.run()` gained an optional `request_id` parameter:
`POST /live/runs` allocates the id, hands back `{run_id, stream_url}`, and
passes the id in. Every existing route (`/runs/{id}`, `/runs/{id}/view`,
the compliance reports) therefore works unchanged on the same id.

Every event is buffered, so an `EventSource` that connects late replays
from the start, and `Last-Event-ID` reconnects resume exactly where they
left off. There is no race to lose.

### 4. Instrumentation is opt-in and cannot fail a run

`run(request, emit=None, request_id=None)` — both keyword, both defaulting
to previous behaviour, so all five existing callers are untouched. Every
emit is wrapped in a blanket `except` that logs and continues. A broken UI
listener must never be able to fail a code generation; the run is the
product, the live view is a window onto it.

### 5. Panels are server-rendered fragments reusing `run_view`'s renderers

The Z3 panel calls `SymbolicDebugger`, which has no client-side
equivalent, and reimplementing the confidence bar in JavaScript would fork
it from the fixed factor ordering `run_view`'s docstring pins down. So
events carry a `html` field built by the *existing* private renderers via
thin public wrappers, and the client does `innerHTML`. One source of truth
for how a panel looks; the live and post-hoc views cannot drift.

### 6. Narration comes from fixed templates, never a second LLM call

Each event carries a plain-language sentence from
`agent/event_narration.py`. A narrator model would produce fluent text with
no guarantee it describes what the pipeline actually did, which is
precisely the failure mode this project exists to argue against. The
templates are also where the NOT_VERIFIED honesty rule is enforced in
user-facing prose: it must never read as anything resembling a pass.

### 7. `RateLimitMiddleware` becomes raw ASGI

`BaseHTTPMiddleware` pumps every response through an anyio task group and
memory stream, which interferes with long-lived streaming responses.
Exempting the SSE path would not have helped — the wrapping happens
regardless of what the middleware decides. It is now a plain ASGI callable
(~35 lines), with the same counters, the same `reset_rate_limit_state()`
test hook, and a prefix exemption for `/live/runs` so an auto-reconnecting
`EventSource` does not burn a participant's quota mid-run.

### 8. T5 condition masking is enforced server-side

The study manipulation (which panel a participant sees) is applied before
the event is buffered: for a suppressed panel the HTML, the underlying
numbers in `data`, *and* the narration string are all replaced. CSS
`display:none` would have been defeated by opening dev tools, silently
corrupting that participant's data point. The condition is drawn per run,
server-side, and stamped onto the study response from the registry — never
accepted from the request body, which would let a participant self-select.

## Consequences

**Gained.** The product's actual differentiator is now visible while it
happens. Traces are persisted per attempt instead of only at the end, so
`/runs/{id}/view` works mid-run and survives a dropped stream. T5 has a
self-serve vehicle, so running it is no longer gated on scheduling calls.

**Given up.** Live runs are per-process, in-memory, and capped
(`VERITYAI_MAX_LIVE_RUNS`, default 4 — daemon threads bypass anyio's
limiter, so this is the only backpressure on the single Ollama instance).
There is no durable queue and no recovery if a worker thread dies; the run
is simply marked failed. This is explicitly *not* the general async job
queue `rest.py`'s module docstring still calls for, and `/generate` keeps
its simple synchronous contract.

**Accepted for the study.** Free-form prompts mean no two participants
judge the same code, so per-sample trust rates across participants — an
analysis item the moderated protocol supports — are unavailable in the
live variant. The moderated 6-sample protocol is therefore kept as the
higher-internal-validity design rather than replaced, and results from the
two vehicles must be reported separately.

## Alternatives rejected

- **WebSocket**: bidirectional machinery for unidirectional traffic.
- **Polling `/runs/{id}`**: no representation for sub-attempt stages;
  latency and request volume for a worse experience.
- **Token-level LLM streaming**: would mean changing
  `neural/ollama_client.py`'s blocking call. Stage-level events answer
  "what is it doing and why" — the actual question — without that surface.
- **Client-rendered panels**: no client-side `SymbolicDebugger`, and it
  would fork the presentation.
- **An LLM narrator for step explanations**: unverifiable prose, contrary
  to the project's premise.
