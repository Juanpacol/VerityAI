"""Shared fixtures.

Kept deliberately small. The pre-pivot suite needed heavy fixtures because
every test path ran through Ollama, Neo4j and a database; the harness core is
pure functions over in-memory objects and a directory of text files, so most
tests need nothing but `tmp_path`.
"""

import pytest

from verityai.context.tokenizer import TokenCounter
from verityai.core.models import ContextItem, ItemKind
from verityai.memory.store import MemoryStore


class FixedCounter(TokenCounter):
    """A counter with one token per whitespace-separated word.

    Used wherever a test asserts on exact token arithmetic. Real tokenizers
    are an implementation detail that would make those assertions brittle and,
    worse, would make a test fail differently depending on whether tiktoken
    happens to be installed.
    """

    def __init__(self):
        super().__init__()
        self._encoder = None
        self.method = "fixed:words"

    def count(self, text: str):
        from verityai.context.tokenizer import TokenCount

        return TokenCount(len(text.split()), self.method)


@pytest.fixture
def counter():
    return FixedCounter()


@pytest.fixture
def store(tmp_path):
    """An initialized `.verity/` in a temporary directory."""
    return MemoryStore.init(tmp_path)


def item(content, kind=ItemKind.AGENT_MESSAGE, index=0):
    """Terse `ContextItem` constructor for tests."""
    return ContextItem(kind=kind, content=content, original_index=index)
