"""Token counting, with the counting method always reported alongside.

The headline claim of this project is a token-savings number. That number is
only worth something if it is measured rather than estimated, and only honest
if the difference is visible. So every count returned here is a `TokenCount`
carrying the method that produced it, and the report layer refuses to print a
bare integer.

Three methods, in descending order of trust:

- `tiktoken:<encoding>` — exact for OpenAI models, and close enough for
  Anthropic ones to be useful (Claude's tokenizer is not public; `cl100k_base`
  typically lands within a few percent on English and code).
- `heuristic:chars/4` — the fallback when tiktoken is not installed. Fine for
  relative comparisons within one run, not for cost estimates.
- `unmeasured` — nothing has counted this yet. Never appears in a report.

The fallback is deliberately *not* silent: `TokenCounter.method` says which
one is in use, and `heuristic` counts are labelled as estimates everywhere
they surface.
"""

import re
from dataclasses import dataclass

# tiktoken is an optional dependency. The harness must work without it —
# degraded, and saying so — rather than refusing to start.
try:  # pragma: no cover - trivial import guard
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False


DEFAULT_ENCODING = "cl100k_base"

# Average characters per token for the fallback. 4.0 is the usual rule of
# thumb for English prose; code runs denser (more punctuation, more short
# tokens), so a single constant under-counts code somewhat. That bias is
# documented rather than tuned away, because tuning it against one corpus
# would just move the error somewhere less visible.
_CHARS_PER_TOKEN = 4.0

_WHITESPACE_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class TokenCount:
    """A token count that cannot be separated from how it was measured."""

    tokens: int
    method: str

    @property
    def is_estimate(self) -> bool:
        return not self.method.startswith("tiktoken:")

    def __str__(self) -> str:
        suffix = " (est.)" if self.is_estimate else ""
        return f"{self.tokens:,} tokens{suffix}"


# Context window sizes, for budget defaults and window-usage reporting.
# Deliberately a small hand-maintained table rather than a network lookup:
# being wrong offline is worse than being slightly stale. Unknown models fall
# back to the conservative default below.
MODEL_WINDOWS: dict[str, int] = {
    "claude-opus-5": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "o200k": 128_000,
}

DEFAULT_WINDOW = 128_000


def window_for_model(model: str | None) -> int:
    """Context window for `model`, or a conservative default.

    Matches on prefix so dated model ids (`claude-haiku-4-5-20251001`)
    resolve without needing a table entry each.
    """
    if not model:
        return DEFAULT_WINDOW
    normalized = model.lower()
    for known, size in MODEL_WINDOWS.items():
        if normalized.startswith(known):
            return size
    return DEFAULT_WINDOW


class TokenCounter:
    """Counts tokens, and reports how.

    Constructed once and passed around rather than called as a free function,
    so that a whole run is guaranteed to use one consistent method — mixing
    an exact count and an estimate inside a single before/after comparison
    would produce a savings number that is pure artefact.
    """

    def __init__(self, model: str | None = None, encoding: str = DEFAULT_ENCODING):
        self.model = model
        self.encoding_name = encoding
        self._encoder = None

        if _TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.get_encoding(encoding)
                self.method = f"tiktoken:{encoding}"
            except Exception:
                # A bad encoding name or a corrupt tiktoken cache must not
                # take the harness down; degrade and say so.
                self._encoder = None
                self.method = "heuristic:chars/4"
        else:
            self.method = "heuristic:chars/4"

    @property
    def is_exact(self) -> bool:
        return self._encoder is not None

    @property
    def window(self) -> int:
        return window_for_model(self.model)

    def count(self, text: str) -> TokenCount:
        """Count tokens in `text`."""
        if not text:
            return TokenCount(0, self.method)

        if self._encoder is not None:
            return TokenCount(len(self._encoder.encode(text, disallowed_special=())), self.method)

        return TokenCount(self._estimate(text), self.method)

    def count_all(self, texts: list[str]) -> TokenCount:
        """Total tokens across `texts`.

        Sums per-item counts rather than counting the concatenation: the
        pipeline reports per-item totals too, and a concatenated count would
        not equal their sum (tokenizers merge across boundaries), which makes
        the arithmetic in a savings table fail to add up.
        """
        return TokenCount(sum(self.count(t).tokens for t in texts), self.method)

    def _estimate(self, text: str) -> int:
        """Character-based fallback.

        Runs of whitespace collapse to one character first — indentation and
        blank lines are cheap in real tokenizers but would otherwise inflate a
        naive `len(text)/4` badly on formatted code, which is most of what
        this tool measures.
        """
        collapsed = _WHITESPACE_RUN.sub(" ", text.strip())
        if not collapsed:
            return 0
        return max(1, round(len(collapsed) / _CHARS_PER_TOKEN))
