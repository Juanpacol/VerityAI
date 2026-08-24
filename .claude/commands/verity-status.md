---
description: Sectioned VerityAI status view against this session's own transcript.
allowed-tools: Bash(verity status:*)
---

Status against *this* session's own transcript (not "most recently touched
file" -- right after a long prior session ends and a new one starts, the
old one's file can still have the newer mtime for a while, which silently
pointed this at the wrong transcript before this fix):

!`verity status "$HOME/.claude/projects/$(echo "${CLAUDE_PROJECT_DIR}" | sed 's/\//-/g')/${CLAUDE_SESSION_ID}.jsonl"`

Present the output above as-is.
