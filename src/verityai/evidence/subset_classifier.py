"""Classifies arbitrary Python code as inside or outside VerityAI's
verifiable subset (ADR-0001) -- the entry point for T3 of the research
roadmap ("what fraction of realistic code actually falls inside the
subset we verify?").

Ground truth comes directly from the real verifier
(`symbolic.ast_to_smt.ASTtoSMTConverter`), not a reimplementation: a
problem is a subset member iff `convert_code(...)` reports zero
non-verifiable nodes. The category bucketing below is reporting-only --
it explains *why* a problem was excluded in human terms, and it can never
override what the converter itself decided.

Categories are derived from the converter's own `reason`/`type` fields
(see `ast_to_smt.py`'s `_mark_non_verifiable` call sites), not an
independent AST walk -- that matters because the converter deliberately
strips docstrings before conversion (`_body_without_docstring`), so an
independent walk that flagged every string constant as "string_ops" would
massively over-count: nearly every HumanEval problem has a docstring, and
none of that is what actually blocks verification.
"""

import ast
from dataclasses import dataclass, field
from typing import Optional

from verityai.symbolic.ast_to_smt import ASTtoSMTConverter

# Ordered (first match wins) substring -> category mapping, built directly
# from the exact `reason` strings the converter raises (see
# symbolic/ast_to_smt.py's `raise VerifiableSubsetViolation(...)` sites).
_REASON_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    ("Method calls not verifiable", "unsupported_call"),
    ("Function call to", "unsupported_call"),
    ("Expression statement", "unsupported_call"),
    ("len() takes", "unsupported_call"),
    ("len() on complex expressions", "container_ops"),
    ("abs() takes", "unsupported_call"),
    ("min() takes", "unsupported_call"),
    ("max() takes", "unsupported_call"),
    ("Builtin", "unsupported_call"),
    ("Variable", "undefined_variable"),
    ("Binary op", "unsupported_operator"),
    ("Comparison", "unsupported_operator"),
    ("BoolOp", "unsupported_operator"),
    ("UnaryOp", "unsupported_operator"),
    ("Augmented op", "unsupported_operator"),
    ("Multiple assignment targets", "unsupported_assignment"),
    ("Assignment target", "unsupported_assignment"),
    ("AugAssign target", "unsupported_assignment"),
    ("Unknown type", "unsupported_type_annotation"),
    ("Non-simple loop target", "unsupported_loop"),
    ("Non-range loop", "container_ops"),  # `for x in some_list` iterates a container
    ("range() with step", "unsupported_loop"),
    ("Expression type", "unsupported_expression"),
]

# Statement-level marks categorized by AST node type -- either because the
# converter marks them with an empty `reason` (While/With/Try), or because
# the generic "Unsupported statement type: X" reason doesn't carry enough
# detail on its own to categorize (Import/ImportFrom/ClassDef etc., all
# common in real-world code but orthogonal to the verifiable-subset
# question this classifier exists to answer).
_TYPE_CATEGORY: dict[str, str] = {
    "While": "while_loop",
    "With": "unsupported_statement",
    "Try": "exceptions",
    "Import": "import_statement",
    "ImportFrom": "import_statement",
    "ClassDef": "class_definition",
    "Global": "unsupported_statement",
    "Nonlocal": "unsupported_statement",
}


def _categorize_reason(reason: str) -> Optional[str]:
    if reason.startswith("Unsupported constant: '") or reason.startswith('Unsupported constant: "'):
        return "string_ops"
    if reason.startswith("Unsupported constant:"):
        return "other_constant"
    for prefix, category in _REASON_CATEGORY_PATTERNS:
        if prefix in reason:
            return category
    return None


def _categorize_node(node: dict) -> str:
    reason = node.get("reason") or ""
    if reason:
        category = _categorize_reason(reason)
        if category is not None:
            return category
    return _TYPE_CATEGORY.get(node.get("type", ""), "other")


def _detect_self_recursion(tree: ast.AST) -> bool:
    """Direct self-recursion only (a function calling its own name) -- not
    mutual recursion across multiple functions. A simplification, not a
    ground-truth claim; this only ever *adds* an informational tag, it
    never changes `subset_member`.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == node.name
                ):
                    return True
    return False


@dataclass
class SubsetClassification:
    """Result of classifying one code sample against the verifiable subset."""

    subset_member: bool
    non_verifiable_nodes: list[dict] = field(default_factory=list)
    exclusion_categories: list[str] = field(default_factory=list)


def classify_problem(code: str) -> SubsetClassification:
    """Classify `code` as inside/outside the verifiable subset.

    Never raises: a syntax error or an unexpected converter failure is
    reported as a distinct exclusion category rather than propagated,
    since this runs unattended over ~1,100 real-world benchmark problems
    the converter was never specifically tuned against.
    """
    normalized = code.replace("\r\n", "\n")

    try:
        tree = ast.parse(normalized)
    except SyntaxError as e:
        return SubsetClassification(
            subset_member=False,
            non_verifiable_nodes=[{"line": e.lineno or 0, "type": "SyntaxError", "reason": str(e)}],
            exclusion_categories=["syntax_error"],
        )

    converter = ASTtoSMTConverter(allow_partial=True)
    try:
        _, non_verifiable_nodes = converter.convert_code(normalized)
    except Exception as e:  # noqa: BLE001 -- classify, don't crash the batch run
        return SubsetClassification(
            subset_member=False,
            non_verifiable_nodes=[{"line": 0, "type": "ConverterError", "reason": str(e)}],
            exclusion_categories=["converter_error"],
        )

    categories = sorted({_categorize_node(node) for node in non_verifiable_nodes})
    if non_verifiable_nodes and _detect_self_recursion(tree):
        categories = sorted(set(categories) | {"recursion"})

    return SubsetClassification(
        subset_member=len(non_verifiable_nodes) == 0,
        non_verifiable_nodes=non_verifiable_nodes,
        exclusion_categories=categories,
    )
