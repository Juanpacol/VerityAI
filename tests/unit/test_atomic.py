"""Tests for atomic_write_text.

The property that matters: a reader never observes a partial write. Since a
real mid-write kill can't be simulated portably in a unit test, this proves
the mechanism that guarantees it -- the previous file survives untouched if
the write step raises before the rename -- rather than the kill itself.
"""

from pathlib import Path

import pytest

from verityai.core.atomic import atomic_write_text


def test_writes_the_content(tmp_path):
    path = tmp_path / "f.txt"

    atomic_write_text(path, "hello")

    assert path.read_text() == "hello"


def test_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "f.txt"

    atomic_write_text(path, "hello")

    assert path.read_text() == "hello"


def test_overwrites_existing_content(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("old")

    atomic_write_text(path, "new")

    assert path.read_text() == "new"


def test_no_temp_file_left_behind_on_success(tmp_path):
    path = tmp_path / "f.txt"

    atomic_write_text(path, "hello")

    leftovers = [p for p in tmp_path.iterdir() if p.name != "f.txt"]
    assert leftovers == []


def test_previous_file_survives_a_write_that_raises(tmp_path, monkeypatch):
    """The failure-injection proof: if the write step raises before the
    rename, the old file must be untouched and no temp file left behind --
    the same guarantee a mid-write kill needs, exercised deterministically."""
    path = tmp_path / "f.txt"
    path.write_text("original")

    import verityai.core.atomic as atomic_module

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(atomic_module.os, "fdopen", boom)

    with pytest.raises(RuntimeError):
        atomic_write_text(path, "new content")

    assert path.read_text() == "original"
    leftovers = [p for p in tmp_path.iterdir() if p.name != "f.txt"]
    assert leftovers == []


def test_temp_file_stays_on_the_same_filesystem_as_the_target(tmp_path, monkeypatch):
    """The rename is only atomic if source and destination share a
    filesystem -- asserted by checking the temp file is a sibling, not in a
    system temp directory."""
    path = tmp_path / "f.txt"
    seen: list[Path] = []

    import verityai.core.atomic as atomic_module

    real_mkstemp = atomic_module.tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        seen.append(Path(name))
        return fd, name

    monkeypatch.setattr(atomic_module.tempfile, "mkstemp", spy)
    atomic_write_text(path, "hello")

    assert seen[0].parent == tmp_path
