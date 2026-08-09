"""AST-based fact extraction for the two KG security rules whose PRE/POST
conditions describe patterns Z3 cannot reason about at all: `SQL Injection
Prevention` and `No Check-Then-Act Race` (see
`kg/seed_data/security_rules.json`). Both rules have existed since the
project's seed data was written, but nothing previously extracted the
facts their `formal_spec` PRE-conditions name (`user_input is untrusted`,
`check_then_act_on_shared_resource`) from real code -- they were prompt
guidance only, never independently checked. This module is T6's prototype
answer to "is pattern-matching enough to close that gap:" it extracts
fact strings that `symbolic.rule_engine.RuleEngine` can consume via its
existing (pre-existing, unmodified) forward-chaining machinery.

Deliberately narrow, not a general SQLi/race-condition detector -- see the
docstring on each function for exactly what it catches and what it
doesn't. Z3 itself cannot help here: microsoft/z3guide's own Strings
theory page (fetched as real evidence, see
`docs/evidence/z3_docs/z3_docs_d192712d2ab1.json`) states its string
solver is "an incomplete heuristic solver" and the combined theory "is
not decidable anyway" -- arbitrary reasoning about untrusted string flow
into a query is not something a formal solver settles, so a narrower,
honest pattern-matcher is the deliberate alternative, not a workaround.
"""

import ast

_SQL_EXECUTE_METHOD_NAMES = frozenset({"execute", "executemany", "executescript"})
_PARAM_PLACEHOLDERS = ("?", "%s")


def _is_dynamic_sql_string(node: ast.expr) -> tuple[bool, str]:
    """Returns (is_dynamically_built, how) for a query-argument expression."""
    if isinstance(node, ast.JoinedStr):
        return True, "sql_query_built_via_fstring"
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            return True, "sql_query_built_via_concatenation"
        if isinstance(node.op, ast.Mod):
            return True, "sql_query_built_via_percent_format"
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return True, "sql_query_built_via_format_call"
    return False, ""


def _literal_string_value(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _dynamic_sql_assignments(func_node: ast.AST) -> dict[str, str]:
    """One-hop resolution: `query = <dynamic string expr>` earlier in the
    same function, so `execute(query)` is still caught even though the
    dynamic expression isn't inline at the call site. Deliberately only
    one hop (the last such assignment to a given name wins, matching
    normal reassignment semantics) -- see the module-level "what this does
    NOT catch" note for the multi-hop/cross-function case this skips.
    """
    dynamic_vars: dict[str, str] = {}
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        is_dynamic, how = _is_dynamic_sql_string(node.value)
        name = node.targets[0].id
        if is_dynamic:
            dynamic_vars[name] = how
        else:
            dynamic_vars.pop(name, None)  # reassigned to something static -- no longer dynamic
    return dynamic_vars


def extract_sql_injection_facts(code: str) -> set[str]:
    """Detects the textbook SQL-injection anti-pattern: a query string built
    by concatenation/f-string/%-format/`.format()` and passed as the first
    argument to a call named `execute`/`executemany`/`executescript`
    (matches sqlite3, psycopg2, MySQLdb, and any DB-API-2.0-shaped client --
    matched by method name only, not by import, since callers rarely type-
    annotate cursor objects). Also resolves one hop through a local
    variable (`query = "..." + x; cursor.execute(query)`), since building
    the query on its own line first is at least as common as inlining it.

    Also recognizes the standard SAFE idiom -- a literal query string using
    `?`/`%s` placeholders passed alongside a second (parameters) argument --
    and emits `uses_parameterized_query` instead, so a caller can tell
    "no dynamic SQL found" apart from "found dynamic SQL, but it's the safe
    parameterized kind."

    What this does NOT catch: query strings assembled across more than one
    hop of variable reassignment, built in a different function/module and
    passed in as a parameter, or via an ORM query builder rather than raw
    string concatenation/interpolation. Purely syntactic -- no real
    data-flow analysis.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    facts: set[str] = set()

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dynamic_vars = _dynamic_sql_assignments(func_node)

        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in _SQL_EXECUTE_METHOD_NAMES):
                continue
            if not node.args:
                continue

            query_arg = node.args[0]
            is_dynamic, how = _is_dynamic_sql_string(query_arg)
            if not is_dynamic and isinstance(query_arg, ast.Name):
                resolved = dynamic_vars.get(query_arg.id)
                if resolved:
                    is_dynamic, how = True, resolved
            if is_dynamic:
                facts.add("sql_query_built_dynamically")
                facts.add(how)
                continue

            literal = _literal_string_value(query_arg)
            if literal and any(p in literal for p in _PARAM_PLACEHOLDERS) and len(node.args) >= 2:
                facts.add("uses_parameterized_query")

    return facts


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _context_manager_is_lock(item: ast.withitem) -> bool:
    ctx = item.context_expr
    target = ctx.func if isinstance(ctx, ast.Call) else ctx
    if isinstance(target, ast.Attribute):
        return "lock" in target.attr.lower()
    if isinstance(target, ast.Name):
        return "lock" in target.id.lower()
    return False


def extract_race_condition_facts(code: str) -> set[str]:
    """Detects the textbook check-then-act race: within one function, an
    `if <key> in <container>:` (or `if <container>.get(<key>):`) test
    followed by a subscript assignment to that same `<container>`,
    anywhere in the function, with no enclosing `with <lock>:` (matched by
    "lock" appearing in the context manager's name/attribute -- e.g.
    `self._lock`, `threading.Lock()`) around either the check or the act.

    This is intentionally the narrowest, most literal reading of the KG
    rule's own precondition (`check_then_act_on_shared_resource`) --
    real race conditions can occur across threads, processes, async tasks,
    or with far subtler interleavings than one function's syntax shows.
    Treat a hit here as "worth a human look," never as a proof of an
    actual race, and treat a miss as "not this exact shape," never as
    "this function has no concurrency bugs."
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    facts: set[str] = set()

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        checked: set[str] = set()
        acted: set[str] = set()
        lock_guarded: set[str] = set()
        _visit_race_stmts(
            func_node.body, guarded=False, checked=checked, acted=acted, lock_guarded=lock_guarded
        )

        unguarded_race = (checked & acted) - lock_guarded
        if unguarded_race:
            facts.add("check_then_act_on_shared_resource")
        elif checked & acted:
            facts.add("check_and_act_combined_atomically")

    return facts


def _visit_race_stmts(
    stmts: list[ast.stmt],
    guarded: bool,
    checked: set[str],
    acted: set[str],
    lock_guarded: set[str],
) -> None:
    """Recursively walks `stmts`, tracking whether each is lexically inside
    a lock-guarded `with` block (`guarded`) so nested checks/acts inherit
    that context correctly -- a flat `ast.walk` can't tell "this If is
    inside that With" from "this If merely comes after that With in
    iteration order," which is exactly the containment `guarded` needs.
    """
    for node in stmts:
        if isinstance(node, ast.If):
            test = node.test
            container = ""
            if (
                isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], (ast.In, ast.NotIn))
            ):
                container = _name_of(test.comparators[0])
            elif (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Attribute)
                and test.func.attr == "get"
                and isinstance(test.func.value, ast.Name)
            ):
                container = test.func.value.id
            if container:
                checked.add(container)
                if guarded:
                    lock_guarded.add(container)
            _visit_race_stmts(node.body, guarded, checked, acted, lock_guarded)
            _visit_race_stmts(node.orelse, guarded, checked, acted, lock_guarded)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    container = target.value.id
                    acted.add(container)
                    if guarded:
                        lock_guarded.add(container)
        elif isinstance(node, ast.With):
            still_guarded = guarded or any(_context_manager_is_lock(item) for item in node.items)
            _visit_race_stmts(node.body, still_guarded, checked, acted, lock_guarded)
        elif isinstance(node, (ast.For, ast.While)):
            _visit_race_stmts(node.body, guarded, checked, acted, lock_guarded)
            _visit_race_stmts(node.orelse, guarded, checked, acted, lock_guarded)
        elif isinstance(node, ast.Try):
            _visit_race_stmts(node.body, guarded, checked, acted, lock_guarded)
            for handler in node.handlers:
                _visit_race_stmts(handler.body, guarded, checked, acted, lock_guarded)
            _visit_race_stmts(node.orelse, guarded, checked, acted, lock_guarded)
            _visit_race_stmts(node.finalbody, guarded, checked, acted, lock_guarded)
