"""Atomic file writes: write to a temp file, then rename over the target.

`Path.write_text` is truncate-then-write -- a process killed between the two
steps leaves a truncated file, and two concurrent writers can interleave.
`os.replace` is atomic on POSIX (a single filesystem rename), so a reader
never observes a partial write: it sees either the old content or the new,
never a mix. The temp file lives in the same directory as the target so the
rename stays on one filesystem (a cross-filesystem rename is not atomic).

This is the one place `write_text` should never be called directly under
`.verity/` — every caller that persists state uses this instead.
"""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
