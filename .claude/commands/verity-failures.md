---
description: List and (with an argument) resolve recorded failures, without leaving this window.
argument-hint: "[resolve N] [note text]"
allowed-tools: Bash(verity failures:*)
---

!`N=$(echo "$ARGUMENTS" | awk '{print $2}'); NOTE=$(echo "$ARGUMENTS" | cut -s -d' ' -f3-); if echo "$ARGUMENTS" | grep -q '^resolve '; then verity failures resolve "$N" --note "$NOTE"; else verity failures; fi`

Present the output above as-is. This ran deterministically -- word-splitting
`$ARGUMENTS` with `set --` was tried first and turned out unreliable across
shell environments, so this avoids it entirely (`awk`/`cut` instead).
