---
description: Multi-dimensional context health against this session's own transcript.
allowed-tools: Bash(verity health:*), Bash(ls:*)
---

Health against the most recent session transcript in this project:

!`PROJ_DIR="$HOME/.claude/projects/$(echo "${CLAUDE_PROJECT_DIR}" | sed 's/\//-/g')"; verity health "$(ls -t "$PROJ_DIR"/*.jsonl 2>/dev/null | head -1)"`

Present the output above as-is.
