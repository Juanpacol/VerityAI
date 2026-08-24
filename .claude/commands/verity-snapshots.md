---
description: List saved VerityAI snapshots. Argument N shows one in full.
argument-hint: "[N]"
allowed-tools: Bash(verity snapshots:*)
---

!`verity snapshots`

If `$ARGUMENTS` is a number, also run `verity snapshots show <N>` with that number
and show its full contents. Otherwise present the list above as-is.
