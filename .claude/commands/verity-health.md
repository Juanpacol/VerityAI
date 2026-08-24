---
description: Multi-dimensional context health against this session's own transcript.
allowed-tools: Bash(verity health:*)
---

Health against *this* session's own transcript (`${CLAUDE_SESSION_ID}`, not
"most recently touched file" -- a stale-mtime file from a just-ended prior
session used to win that race silently):

!`verity health "$HOME/.claude/projects/$(echo "${CLAUDE_PROJECT_DIR}" | sed 's/\//-/g')/${CLAUDE_SESSION_ID}.jsonl"`

Present the output above as-is.
