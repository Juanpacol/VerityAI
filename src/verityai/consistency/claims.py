"""Pulling checkable claims out of agent-produced text, deterministically.

Scope is narrow on purpose, the same discipline as ADR-0001's verifiable
subset: rather than attempt to parse arbitrary prose for assertions (which
would need a model, and would need to guess), extraction only fires on
**backtick-quoted spans** and a **small, closed set of relation phrases**. An
agent writing about code in markdown — which is how Claude Code, Codex and
Cursor all format their output — puts identifiers and paths in backticks
constantly. That convention is the whole signal this module needs, and it
costs nothing to detect.

A claim this module fails to extract is simply not checked. That is the
correct failure mode: an under-approximation that never reports a false
positive, rather than an eager extractor that invents claims out of ordinary
prose and then "catches" contradictions that were never asserted.
"""

import re

from verityai.core.models import Claim, ClaimKind

_BACKTICK = re.compile(r"`([^`]+)`")

# A dotted/underscored Python-ish identifier, with an optional call marker.
# Deliberately excludes bare single words with no punctuation (`true`, `run`)
# unless they end in `()` -- a bare English word in backticks is far more
# often a literal value or a shell command than a symbol reference.
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(\(\))?$")

_FILE_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".md",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".txt",
    ".cfg",
    ".ini",
)

# Relation phrases mapped to the graph edge kind they claim. Only phrases with
# an unambiguous edge-kind mapping are included -- "depends on" and "uses"
# could mean CALLS, IMPORTS, or nothing checkable, so they are left
# unrecognized rather than mapped to a guess.
_RELATIONS = {
    "calls": "calls",
    "inherits from": "inherits",
    "extends": "inherits",
}

# Both subject and target MUST be backtick-quoted -- not optional. An early
# version made the backticks optional and matched "The `GraphStore` class
# inherits from `Base`" with subject="class" (the bare word right before the
# verb phrase) instead of `GraphStore`. Requiring backticks on both sides
# means the regex can only ever bind to something the writer explicitly
# marked as code, which is the same discipline the rest of this module
# already applies to bare SYMBOL_EXISTS and FILE_EXISTS spans. A short,
# whitelisted noun (class/function/method/object) is still allowed to sit
# between the subject and the verb, since "`GraphStore` class inherits from"
# is how people actually write it.
_RELATION_PATTERN = re.compile(
    r"`(?P<subject>[\w.]+)`\s*(?:class|function|method|object)?\s+(?P<relation>"
    + "|".join(re.escape(phrase) for phrase in _RELATIONS)
    # The target may be a symbol (`[\w.]+`) or a file path -- `/` and `-`
    # are needed for the latter (e.g. `billing/tax_rates.py`). Without them
    # a claim like "`apply_tax` calls `billing/tax_rates.py`" never matches
    # at all and silently decomposes into two independent, both-true
    # existence checks -- see ADR-0018.
    + r")\s+`(?P<target>[\w./-]+)`",
    re.IGNORECASE,
)


def looks_like_path(token: str) -> bool:
    return "/" in token or token.lower().endswith(_FILE_EXTENSIONS)


# Kept as an alias: this was private until check.py needed the same test to
# route a relation claim to the file-import checker instead of the
# symbol-relation checker (ADR-0018's fix).
_looks_like_path = looks_like_path


def looks_like_bare_name(subject: str, raw_text: str) -> bool:
    """True if this token carries no marker distinguishing it from an
    ordinary local variable name -- no call parens, no dotted/qualified
    path, no CamelCase.

    This is deliberately the *same* shape a hallucinated bare function name
    has (ADR-0018's pilot caught 14/14 invented names, all in exactly this
    shape: plain snake_case, no punctuation, no parens). There is no
    lexical signal that separates "someone backtick-quoted a local variable
    for emphasis" from "someone invented a function name" -- so this is a
    caveat signal for the checker's explanation text, never a reason to
    change a verdict or confidence. Doing that would have silently traded
    away the very recall ADR-0018 measured.
    """
    return "." not in subject and "()" not in raw_text and not any(c.isupper() for c in subject)


def _looks_like_symbol(token: str) -> bool:
    return bool(_SYMBOL.match(token)) and (
        "." in token or "_" in token or token.endswith("()") or any(c.isupper() for c in token[1:])
    )


def extract_claims(text: str) -> list[Claim]:
    """Extract SYMBOL_EXISTS, FILE_EXISTS and SYMBOL_RELATION claims from `text`.

    Order: relation phrases first (they subsume the symbol mentions inside
    them), then remaining standalone backtick spans. A span already consumed
    by a relation match is not re-extracted as a bare SYMBOL_EXISTS claim --
    that would double-count one assertion as two, and a checker finding the
    relation supported would say nothing about the bare-existence duplicate
    finding it contradicted for an unrelated reason.
    """
    claims: list[Claim] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _RELATION_PATTERN.finditer(text):
        subject = match.group("subject")
        target = match.group("target")
        relation = _RELATIONS[match.group("relation").lower()]

        claims.append(
            Claim(
                kind=ClaimKind.SYMBOL_RELATION,
                subject=subject,
                relation=relation,
                target=target,
                raw_text=match.group(0),
            )
        )
        consumed_spans.append(match.span())

    def _is_consumed(start: int, end: int) -> bool:
        return any(cs <= start and end <= ce for cs, ce in consumed_spans)

    for match in _BACKTICK.finditer(text):
        if _is_consumed(*match.span()):
            continue

        token = match.group(1).strip()
        if not token:
            continue

        if _looks_like_path(token):
            claims.append(Claim(kind=ClaimKind.FILE_EXISTS, subject=token, raw_text=match.group(0)))
        elif _looks_like_symbol(token):
            claims.append(
                Claim(
                    kind=ClaimKind.SYMBOL_EXISTS,
                    subject=token.removesuffix("()"),
                    raw_text=match.group(0),
                )
            )

    return claims
