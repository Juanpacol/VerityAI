---
description: List and (with an argument) resolve recorded failures, without leaving this window.
argument-hint: "[resolve N] [note text]"
allowed-tools: Bash(verity failures:*)
---

Current failures:

!`verity failures`

If `$ARGUMENTS` starts with "resolve", run `verity failures resolve <N> --note "<rest>"`
using the number and note text from `$ARGUMENTS`, then show the updated list.
Otherwise, just present the list above as-is -- do not summarize or invent commentary
beyond what the output already says.
