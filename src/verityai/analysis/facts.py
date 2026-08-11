"""AST-based fact extraction for three security patterns: SQL injection,
check-then-act races, and shell commands built from non-literal input.

Emits fact strings (`user_input is untrusted`,
`check_then_act_on_shared_resource`) that
`reliability.rule_engine.RuleEngine` consumes through its forward-chaining
machinery, and that `reliability.security` turns into findings.

Deliberately narrow, not a general SQLi, race or shell-injection detector --
each function's own docstring states exactly what it catches and what it
misses. That narrowness is the design, not a shortcut: this module is what
remained after T6 found that formal methods could not cover these patterns
at all. Z3's own documentation describes its string solver as "an incomplete
heuristic solver" over a combined theory that "is not decidable anyway",
so tracking untrusted string flow into a query is not something a solver
settles. A narrow pattern-matcher that declares its blind spots is the
honest alternative. See `docs/RESEARCH_FINDINGS_LEGACY.md` (T6) for the
finding, and ADR-0005 for the pivot that followed it.
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


_SHELL_CALL_METHOD_NAMES = frozenset({"run", "call", "check_call", "check_output", "Popen"})


def _shell_true_keyword(call: ast.Call) -> bool:
    return any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _command_argument(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "args":
            return kw.value
    return None


def extract_shell_command_facts(code: str) -> set[str]:
    """Detects a `subprocess.run`/`call`/`check_call`/`check_output`/`Popen`
    call with `shell=True` whose command argument is not a plain string
    literal -- built via f-string/concatenation/%-format/`.format()`, or
    passed through a variable, exactly the shapes `_is_dynamic_sql_string`
    already recognizes for query strings, reused here for command strings.
    `shell=True` hands the string to the platform shell, so any of those
    construction methods puts whatever built the dynamic portion in a
    position to inject shell metacharacters (`;`, `|`, backticks) rather
    than just arguments.

    Also recognizes the standard SAFE idiom -- a call with no `shell=True`
    (shell absent or explicitly `False`) whose command is passed as a list
    of arguments rather than a single string -- and emits
    `shell_disabled_or_args_list`, matching `uses_parameterized_query`'s
    role for the SQL rule: "no dynamic shell command found" is a different
    claim from "found one, but it's the safe list-argument kind."

    What this does NOT catch: `os.system` (always shells out, no `shell=`
    keyword to match on -- out of scope for this rule, not silently
    swallowed); a command string built across more than one hop of variable
    reassignment; or whether the dynamic portion actually originates from
    untrusted input rather than hardcoded, trusted values -- purely
    syntactic, no real data-flow analysis, same limits as
    `extract_sql_injection_facts`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    facts: set[str] = set()

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in _SHELL_CALL_METHOD_NAMES):
                continue

            command = _command_argument(node)
            if command is None:
                continue

            if _shell_true_keyword(node):
                is_dynamic, _ = _is_dynamic_sql_string(command)
                literal = _literal_string_value(command)
                if is_dynamic or (isinstance(command, ast.Name) and not literal):
                    facts.add("shell_command_from_nonliteral")
            elif isinstance(command, ast.List):
                facts.add("shell_disabled_or_args_list")

    return facts
