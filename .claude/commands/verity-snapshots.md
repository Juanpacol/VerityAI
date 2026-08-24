---
description: List saved VerityAI snapshots, or show one's full contents by number.
argument-hint: "[N]"
allowed-tools: Bash(verity snapshots:*)
---

!`if [ -n "$ARGUMENTS" ]; then verity snapshots show $ARGUMENTS; else verity snapshots; fi`

Present the output above as-is. This ran deterministically -- it does not
depend on you reading `$ARGUMENTS` and deciding what to run.
