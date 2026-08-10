#!/usr/bin/env bash
# Shared trial setup for the recovery-after-reset pilots (4, 5, 6 -- ADR-0015,
# ADR-0016, ADR-0017).
#
# All three ask the same question with a different bug, so they need the same
# two conditions built the same way:
#
#   trials/naive_N/   a plain copy of fixture_repo/, with no .verity/ state at
#                     all -- as if a context reset wiped everything and left no
#                     recovery mechanism behind.
#   trials/verity_N/  the same copy, plus a .verity/ directory pre-loaded with
#                     the investigation a prior agent supposedly already did:
#                     the task, its next action, the root-cause decision, and
#                     the supporting discovery.
#
# That phase-A investigation is written through the real `verity task` and
# `verity remember` CLI rather than hand-written JSON, so the pilot exercises
# the same write path an agent would. It is deliberately *fabricated* rather
# than produced by a live agent: a two-live-agent design would confound "did
# the first agent investigate well" with "does recovering its work help", and
# only the second question is being measured.
#
# Callers set five variables and call `setup_phase_a`. See any pilot's
# setup_phase_a.sh for the shape.

setup_phase_a() {
  local required=(PHASE_A_TASK PHASE_A_NEXT PHASE_A_FILE PHASE_A_DECISION PHASE_A_DISCOVERY)
  for var in "${required[@]}"; do
    if [[ -z "${!var:-}" ]]; then
      echo "setup_phase_a: $var is not set" >&2
      return 1
    fi
  done

  # ADR-0021 (Phase 0 truth repair): a re-run of this function against
  # pilots 4/5/6 silently `rm -rf`'d trials/ that held the post-trial,
  # agent-fixed code -- the only evidence for that pilot's published tool-call
  # numbers. Nothing under trials/ is git-tracked (see .gitignore), so once
  # deleted, it was gone. Refuse to repeat that unless the caller explicitly
  # opts in with FORCE_TRIALS_RESET=1, so wiping a trials/ directory
  # containing real trial results is a deliberate act, not a side effect of
  # re-running the setup script.
  if [[ -d trials && "${FORCE_TRIALS_RESET:-0}" != "1" ]]; then
    echo "setup_phase_a: trials/ already exists and would be destroyed." >&2
    echo "  If this directory holds real trial results, back it up first --" >&2
    echo "  nothing under trials/ is git-tracked, so this is the only copy." >&2
    echo "  Set FORCE_TRIALS_RESET=1 to proceed anyway." >&2
    return 1
  fi

  rm -rf trials
  mkdir -p trials

  for i in 1 2 3 4 5; do
    cp -r fixture_repo "trials/naive_$i"

    cp -r fixture_repo "trials/verity_$i"
    (
      cd "trials/verity_$i"
      verity init >/dev/null
      verity task "$PHASE_A_TASK" --next "$PHASE_A_NEXT" --file "$PHASE_A_FILE"
      verity remember decision "$PHASE_A_DECISION"
      verity remember discovery "$PHASE_A_DISCOVERY"
    )
    echo "prepared: naive_$i, verity_$i"
  done
}
