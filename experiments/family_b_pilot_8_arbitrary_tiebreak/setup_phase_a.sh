#!/usr/bin/env bash
# Pilot 8's trial setup. Structure is shared with pilots 4-7 -- see
# ../lib/setup_phase_a.sh for what the two conditions are and why the
# phase-A investigation is fabricated rather than agent-produced.
#
# Pilot 7 (ADR-0019) tested a real code-unresolvable ambiguity but picked
# one ("grace period") with a strong linguistic convention -- every trial
# guessed the same, correct answer unprompted, so no split appeared. This
# fixture's ambiguity carries no such convention: when two candidates tie
# on `score`, which wins is a pure business rule (lower `id` wins) with
# nothing in the domain (opaque numeric IDs, no suggestive naming)
# pointing either way. The hidden test also deliberately lists the
# lower-id candidate SECOND, so the laziest correct-looking fix
# (`max(candidates, key=lambda c: c["score"])`) returns the WRONG one on a
# tie -- Python's `max` keeps the first-seen maximum, which is the
# higher-id candidate here. An agent has to have actually thought about
# ties, not just written a plausible one-liner, to get this right.
set -euo pipefail
cd "$(dirname "$0")"
source ../lib/setup_phase_a.sh

PHASE_A_TASK="Fix failing test test_highest_score_wins in tests/test_allocation.py"
PHASE_A_NEXT="Implement pick_winner() in allocation/pick_winner.py to return the candidate with the highest score, with an explicit tie-break: on a tie, the candidate with the LOWER id wins"
PHASE_A_FILE="allocation/pick_winner.py"
PHASE_A_DECISION="pick_winner() in allocation/pick_winner.py currently ignores score entirely (returns candidates[0]) and needs the real selection logic. Business rule, confirmed with the allocation team: highest score wins; on a tie in score, the candidate with the LOWER id wins. This is an arbitrary policy choice with no natural default -- do not assume a plain max(candidates, key=lambda c: c['score']) handles it correctly, since Python's max() keeps whichever tied candidate appears first in the input list, which does not reliably match id order."
PHASE_A_DISCOVERY="There is no other logic to fix in allocation/pick_winner.py -- the only change needed is implementing the comparison, including the tie-break on id."

setup_phase_a
