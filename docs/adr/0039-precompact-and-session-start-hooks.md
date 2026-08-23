# ADR-0039: automatic capture via `PreCompact`/`SessionStart`, not agent-remembered capture

- **Status**: Accepted
- **Date**: 2026-08-23
- **Context**: raised directly by the user mid-session, reviewing this
  project's own hardening pass: every path into `.verity/` — `verity
  remember`, the MCP `remember` op, even the fabricated-memory pilots this
  project ran on itself — depends on the agent *deciding*, mid-session, to
  write something down. Nothing enforces that it does. The fresh-agent
  pilots in `experiments/` demonstrate the failure mode directly: a `naive`
  condition with no such discipline leaves nothing behind, and even a
  `verity` condition depends on the agent remembering to call `remember`
  before whatever it learned gets compacted away.

## The gap

`family_b_pilot_11_real_compaction/` (n=1, same session) made this
concrete: a real Claude Code session, grown past genuine compaction, lost a
specific detail the agent never explicitly wrote to `.verity/` — the detail
existed only in conversational context, and compaction summarized past it.
`verity handoff` could not recover what was never recorded. No amount of
polishing the *recall* side (`verity handoff`, MCP `session(op="handoff")`)
fixes a *capture* side that is entirely opt-in and entirely the agent's to
remember or forget.

## Decision

Two Claude Code hooks, both read-only with respect to the session and
neither ever blocking:

**`PreCompact`** (`verity hooks precompact`, matcher `manual|auto`) reads
`transcript_path` — the one thing this hook can still reach that the
agent's own head cannot reach after compaction — through the same pipeline
`verity context` already uses: `context/ingest_claude_code.parse_jsonl`
then `context/classify.classify_all`. Every item the classifier calls
`CRITICAL` gets persisted as a `Discovery` (deduplicated against what is
already on file, tagged `source="hook:precompact"`), and the resulting
state is snapshotted. This closes the gap independent of whether the agent
ever called `remember` — the classifier's CRITICAL rules (explicit markers,
financial figures, recency) are exactly the ones
[[0033-user-messages-are-not-unconditionally-critical|ADR-0033]] fixed to
mean something, so this hook inherits that fix rather than re-implementing
relevance judgment.

**`SessionStart`** (`verity hooks session-start`, matcher `compact`) prints
the unbudgeted handoff as plain stdout when `source == "compact"` — Claude
Code adds a `SessionStart` hook's plain stdout as context the model sees,
so a session resuming after compaction gets VerityAI's persisted state
without needing to think to ask for it. A normal `startup`/`resume` session
(not a post-compaction one) gets nothing extra; printing a handoff on every
session start would be noise, not recovery.

Neither hook ever exits 2 (Claude Code's documented mechanism for a
`PreCompact` hook to block compaction outright). A capture failure — no
`.verity/`, an unreadable transcript, corrupt state — degrades to "nothing
extra was saved," reported on stderr/skipped silently, never to a stuck
session. `SnapshotManager.create()`'s [[0037-parse-report-discharges-invariants-5-and-6|ADR-0037]]
refusal on corrupt state is caught and swallowed here specifically for that
reason: corruption already has its own loud channel (`verity health`), and
a background hook is not the place to surface it a second time.

**Installation**: `verity hooks install [path]` merges the two hook entries
into `.claude/settings.json`, reading the existing file first (if any) so
unrelated keys — permissions, other hooks — survive untouched, and is
idempotent (checked by command substring, not blind append). This project
now runs `verity hooks install .` on itself, in both `Makefile`'s `dogfood`
target and CI's dogfood job — the same principle CLAUDE.md already states
for `verity reliability architecture`: a check the project does not apply
to itself is not trustworthy applied to anyone else's.

## Consequences

- Manually verified end-to-end: a fabricated transcript containing a
  `DECISION:`-marked line, never passed to `verity remember`, produces a
  persisted `Discovery` and a numbered snapshot after `verity hooks
  precompact` runs against it; a subsequent `verity hooks session-start`
  with `source=compact` prints that discovery back inside the handoff.
- `tests/unit/test_hooks.py` and `tests/integration/test_cli_hooks.py`
  cover: capture from an unremembered transcript, idempotency of repeated
  captures and repeated installs, every skip path (no store, no
  `transcript_path`, missing file, non-session format) staying non-fatal,
  `resume_context`'s `source != "compact"` no-op, and `install`'s merge
  preserving pre-existing unrelated settings and hooks.
- **Named limitation**: this closes the *capture* gap for content that
  actually appeared in the session transcript before compaction. It cannot
  recover something an agent never said out loud at all — a silent internal
  judgment call the model made and acted on without narrating it leaves
  nothing in `transcript_path` for the classifier to find. The hook makes
  "the agent forgot to call `remember`" survivable; it does not make an
  agent's unstated reasoning inspectable.
- **Named limitation, cost**: `--autocompact`'s documented floor is 100k
  tokens — there is no cheaper way to trigger genuine automatic compaction
  for testing this at scale, which is why
  `family_b_pilot_11_real_compaction/` stopped at n=1 rather than the
  N≥5 `docs/BENCHMARK_PROTOCOL.md` asks for. A proper Family B measurement
  of this specific mechanism is future work, not yet done.
