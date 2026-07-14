"""Convert Python AST to Z3 SMT constraints (verifiable subset).

Assignment semantics use lightweight SSA (static single assignment):
each write to a name creates a *new* versioned Z3 variable rather than
reusing the same one. This is required for soundness — without it,
`x = x + 1` would translate to the constraint `x == x + 1`, which is
unsatisfiable for every integer and would make the converter report
correct code as failing verification.
"""

import ast
import logging
from typing import Any, Optional

from z3 import (
    And,
    Bool,
    If,
    Implies,
    Int,
    IntVal,
    Not,
    Or,
    Real,
    RealVal,
)

logger = logging.getLogger(__name__)


class VerifiableSubsetViolation(Exception):
    """Raised when code contains non-verifiable constructs."""

    pass


class ASTtoSMTConverter:
    """Convert Python AST to Z3 constraints (verifiable subset only)."""

    VERIFIABLE_BUILTINS = {"len", "range", "abs", "min", "max", "int", "bool"}

    def __init__(self, allow_partial: bool = True):
        """Initialize converter.

        Args:
            allow_partial: If True, mark non-verifiable constructs as PARTIAL
                and continue. If False, raise on first non-verifiable construct.
        """
        self.allow_partial = allow_partial
        self.variables: dict[str, Any] = {}  # var name -> CURRENT (latest) Z3 binding
        self._variable_versions: dict[str, int] = {}  # var name -> SSA version counter
        self._variable_types: dict[str, str] = {}  # var name -> inferred type
        self.constraints: list[Any] = []
        self.non_verifiable_nodes: list[dict] = []
        # ADR-0002: parameterized verification. `parameters` names the free
        # (unconstrained) variables bound by _bind_parameters; when non-empty,
        # verify_python_snippet checks each entry in `assert_properties` for
        # VALIDITY (via Z3Engine.verify_property) instead of running a plain
        # satisfiability check over `constraints`. `path_constraints` mirrors
        # `constraints` but never includes assert-derived expressions, so
        # proving one assert never assumes another assert is true.
        self.parameters: list[str] = []
        self.assert_properties: list[tuple[Any, list[Any]]] = []
        self.path_constraints: list[Any] = []

    def convert_code(self, code_str: str) -> tuple[list[Any], list[dict]]:
        """Convert Python code string to Z3 constraints.

        Processes top-level module statements and, for each function
        definition, its body statements — each exactly once. (An earlier
        implementation used `ast.walk`, which visits nested statements
        twice: once directly and once again via the parent If/For's own
        body-processing, silently over-constraining conditional branches.)

        Args:
            code_str: Python code as string

        Returns:
            (constraints, non_verifiable_nodes). See ADR-0002 and the
            `parameters`/`assert_properties`/`path_constraints` attributes
            for the additional parameterized-verification data this also
            populates (not part of the return tuple, to keep this method's
            signature unchanged for existing callers).
        """
        tree = ast.parse(code_str)
        self.constraints = []
        self.variables = {}
        self._variable_versions = {}
        self._variable_types = {}
        self.non_verifiable_nodes = []
        self.parameters = []
        self.assert_properties = []
        self.path_constraints = []

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self._bind_parameters(node)
                self._process_function(node)
                self._process_statements(self._body_without_docstring(node))
            else:
                self._process_statements([node])

        return self.constraints, self.non_verifiable_nodes

    def _body_without_docstring(self, node: ast.FunctionDef) -> list[ast.stmt]:
        """Drop a leading docstring so it isn't processed as a (non-verifiable)
        expression statement -- a bare string literal carries no constraint
        either way, so marking it non-verifiable was pure noise, not a
        meaningful degradation.
        """
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[1:]
        return body

    def _bind_parameters(self, node: ast.FunctionDef) -> None:
        """Bind each function parameter as a free (unconstrained) Z3 variable.

        See ADR-0002. Unlike a local variable's SSA binding, a parameter
        binding carries no equality constraint — its value is unknown,
        which is exactly why an assert referencing it must be checked for
        validity (does it hold for every value?) rather than satisfiability
        of the whole conjunction. Only `node.args.args` (positional-or-
        keyword parameters) are bound; *args/**kwargs/positional-only/
        keyword-only parameters are left unbound (same "variable not
        defined" degradation as before this existed).

        Defaults every parameter to type "int" — no annotation-based type
        inference yet, consistent with the rest of the converter's default.
        """
        for arg in node.args.args:
            self._new_binding(arg.arg, "int")
            self.parameters.append(arg.arg)

    def _process_statements(
        self,
        stmts: list[ast.stmt],
        collector: Optional[list[Any]] = None,
        path_collector: Optional[list[Any]] = None,
        branch_assumptions: Optional[list[Any]] = None,
    ) -> None:
        """Process a flat list of statements in lexical order.

        Args:
            stmts: Statements to process
            collector: If provided, append generated constraints here instead
                of self.constraints. Used by _process_if to scope a branch's
                constraints before wrapping them in an Implies, and threaded
                through to nested for-loops so a loop inside an if-branch is
                scoped correctly too.
            path_collector: Parallel to `collector` but for assert-free path
                facts only (ADR-0002) — mirrors `collector`'s scoping, feeds
                self.path_constraints at the top level.
            branch_assumptions: Z3 boolean expressions for the branch/loop
                conditions active at this point (ADR-0002) — recorded
                alongside each assert found here so a parameterized assert
                nested in a branch can be checked against them.
        """
        target_list = collector if collector is not None else self.constraints
        path_target_list = path_collector if path_collector is not None else self.path_constraints
        branch_assumptions = branch_assumptions or []

        for stmt in stmts:
            try:
                constraint: Optional[Any] = None
                is_path_fact = True  # False for Assert: never a path fact (ADR-0002)

                if isinstance(stmt, ast.Assign):
                    constraint = self._process_assignment(stmt)
                elif isinstance(stmt, ast.AugAssign):
                    constraint = self._process_aug_assign(stmt)
                elif isinstance(stmt, ast.Assert):
                    constraint = self._process_assert(stmt, branch_assumptions)
                    is_path_fact = False
                elif isinstance(stmt, ast.If):
                    constraint, path_constraint, merge_constraints = self._process_if(
                        stmt, branch_assumptions=branch_assumptions
                    )
                    if path_constraint is not None:
                        path_target_list.append(path_constraint)
                    if constraint is not None:
                        target_list.append(constraint)
                    # Phi-merge facts (a variable assigned in either/both
                    # branches, unified via Z3 If()) are unconditionally
                    # true by construction -- not branch-scoped, so they go
                    # in both accumulators directly, unlike the Implies-
                    # wrapped branch bodies above.
                    for merge_constraint in merge_constraints:
                        target_list.append(merge_constraint)
                        path_target_list.append(merge_constraint)
                    continue
                elif isinstance(stmt, ast.For):
                    self._process_for(
                        stmt,
                        collector=collector,
                        path_collector=path_collector,
                        branch_assumptions=branch_assumptions,
                    )
                    continue
                elif isinstance(stmt, ast.Return):
                    continue  # Return itself adds no constraint in this subset
                elif isinstance(stmt, ast.FunctionDef):
                    continue  # Nested defs: metadata already handled separately
                elif isinstance(stmt, ast.Expr):
                    self._mark_non_verifiable(
                        stmt, "Expression statement (e.g. function call) not verifiable"
                    )
                    continue
                elif isinstance(stmt, (ast.While, ast.With, ast.Try)):
                    self._mark_non_verifiable(stmt)
                    continue
                else:
                    self._mark_non_verifiable(
                        stmt, f"Unsupported statement type: {type(stmt).__name__}"
                    )
                    continue

                if constraint is not None:
                    target_list.append(constraint)
                    if is_path_fact:
                        path_target_list.append(constraint)

            except VerifiableSubsetViolation as e:
                if not self.allow_partial:
                    raise
                self._mark_non_verifiable(stmt, str(e))

    def _process_function(self, node: ast.FunctionDef) -> None:
        """Extract PRE/POST/INV annotations from a function's docstring.

        PRE is wired in (ADR-0002): parsed as a Python expression and added
        to path_constraints as an assumption, since almost every realistic
        guard assert (e.g. `assert denominator != 0`) is not a tautology
        and would otherwise report FAIL against a totally unconstrained
        parameter -- PRE lets generated code state the contract its guards
        are meant to enforce. POST/INV are still only recorded, not wired:
        a return-value postcondition needs a way to name "the return
        value" in the expression grammar, tracked as separate follow-up.
        """
        docstring = ast.get_docstring(node)
        if not docstring:
            return

        pre_spec = self._extract_docstring_spec(docstring, "PRE")
        if pre_spec:
            try:
                pre_ast = ast.parse(pre_spec, mode="eval").body
                pre_z3 = self._convert_expr(pre_ast)
                self.path_constraints.append(pre_z3)
                self.constraints.append(pre_z3)
            except (SyntaxError, VerifiableSubsetViolation):
                # Best-effort: an unparseable/non-verifiable PRE spec is
                # skipped rather than crashing -- fails conservatively
                # toward "prove more" (asserts stay unassisted), never
                # toward silently accepting less. See ADR-0002 scope limits.
                pass

        self._extract_docstring_spec(docstring, "POST")
        self._extract_docstring_spec(docstring, "INV")

    def _new_binding(self, var_name: str, var_type: str) -> Any:
        """Create a new SSA-versioned Z3 variable and bind it as var_name's current value.

        Args:
            var_name: Python variable name
            var_type: "int", "bool", or "float"

        Returns:
            The newly created (and now current) Z3 variable
        """
        version = self._variable_versions.get(var_name, 0) + 1
        self._variable_versions[var_name] = version
        versioned_name = var_name if version == 1 else f"{var_name}__v{version}"

        if var_type == "int":
            new_var = Int(versioned_name)
        elif var_type == "bool":
            new_var = Bool(versioned_name)
        elif var_type == "float":
            new_var = Real(versioned_name)
        else:
            raise VerifiableSubsetViolation(f"Unknown type '{var_type}' for variable {var_name}")

        self.variables[var_name] = new_var
        self._variable_types[var_name] = var_type
        return new_var

    def _process_assignment(self, node: ast.Assign) -> Optional[Any]:
        """Convert assignment to a Z3 equality constraint using SSA versioning."""
        if len(node.targets) != 1:
            raise VerifiableSubsetViolation("Multiple assignment targets not supported")

        target = node.targets[0]
        if not isinstance(target, ast.Name):
            raise VerifiableSubsetViolation(f"Assignment target {type(target)} not supported")

        var_name = target.id

        # Evaluate RHS BEFORE rebinding var_name, so a self-reference like
        # `x = x + 1` correctly reads the PREVIOUS version of x.
        z3_value = self._convert_expr(node.value)

        var_type = self._variable_types.get(var_name) or self._infer_type(node.value)
        new_var = self._new_binding(var_name, var_type)

        return new_var == z3_value

    def _process_aug_assign(self, node: ast.AugAssign) -> Optional[Any]:
        """Convert augmented assignment (x += expr) to a Z3 constraint using SSA versioning."""
        target = node.target
        if not isinstance(target, ast.Name):
            raise VerifiableSubsetViolation(f"AugAssign target {type(target)} not supported")

        var_name = target.id
        if var_name not in self.variables:
            raise VerifiableSubsetViolation(
                f"Variable {var_name} used in augmented assignment before initial definition"
            )

        old_var = self.variables[var_name]
        rhs = self._convert_expr(node.value)

        if isinstance(node.op, ast.Add):
            computed = old_var + rhs
        elif isinstance(node.op, ast.Sub):
            computed = old_var - rhs
        elif isinstance(node.op, ast.Mult):
            computed = old_var * rhs
        elif isinstance(node.op, ast.FloorDiv):
            computed = old_var / rhs
        elif isinstance(node.op, ast.Mod):
            computed = old_var % rhs
        else:
            raise VerifiableSubsetViolation(f"Augmented op {type(node.op)} not supported")

        var_type = self._variable_types.get(var_name, "int")
        new_var = self._new_binding(var_name, var_type)

        return new_var == computed

    def _process_assert(self, node: ast.Assert, branch_assumptions: list[Any]) -> Optional[Any]:
        """Convert assert statement to a Z3 constraint (unchanged return value,
        for the existing satisfiability-based path) AND record it as a
        property to prove (ADR-0002), paired with the branch/loop conditions
        active at this point -- used by verify_python_snippet's parameterized
        check, which proves each property given path_constraints +
        branch_assumptions rather than treating it as a plain conjunct.
        """
        property_expr = self._convert_expr(node.test)
        self.assert_properties.append((property_expr, list(branch_assumptions)))
        return property_expr

    def _process_if(
        self, node: ast.If, branch_assumptions: Optional[list[Any]] = None
    ) -> tuple[Optional[Any], Optional[Any], list[Any]]:
        """Convert if statement to Z3 constraints, with proper phi-merging.

        Body/orelse statements are processed into isolated constraint lists
        (not self.constraints directly), then wrapped in Implies so they only
        hold conditionally on the branch actually being taken.

        Phi-merging: `self.variables` is snapshotted before each branch and
        restored afterward, so the two branches never see each other's
        assignments (previously, whichever branch was processed *last* would
        silently overwrite `self.variables[name]` for any name assigned in
        both branches, orphaning the other branch's binding with no real
        constraint linking it to anything downstream -- a latent soundness
        bug affecting any assert *after* an if/else that assigns the same
        variable both ways). Any name touched by either branch gets a fresh
        merged binding `new_var == If(test, then_value, else_value)`,
        returned as `merge_constraints` -- true unconditionally, not scoped
        to either branch.

        Returns (constraint, path_constraint, merge_constraints): the first
        two are the same Implies-wrapped result computed twice in parallel
        -- once over everything (existing self.constraints/satisfiability
        path) and once over only the assert-free path facts (ADR-0002, for
        path_constraints). Nested asserts see the branch's test (or its
        negation) added to their branch_assumptions.
        """
        branch_assumptions = branch_assumptions or []
        test = self._convert_expr(node.test)
        pre_if_variables = dict(self.variables)

        body_constraints: list[Any] = []
        path_body_constraints: list[Any] = []
        self._process_statements(
            node.body,
            collector=body_constraints,
            path_collector=path_body_constraints,
            branch_assumptions=branch_assumptions + [test],
        )
        body_end_variables = dict(self.variables)
        self.variables = dict(pre_if_variables)

        else_constraints: list[Any] = []
        path_else_constraints: list[Any] = []
        self._process_statements(
            node.orelse,
            collector=else_constraints,
            path_collector=path_else_constraints,
            branch_assumptions=branch_assumptions + [Not(test)],
        )
        else_end_variables = dict(self.variables)
        self.variables = dict(pre_if_variables)

        merge_constraints = self._merge_branch_variables(
            test, pre_if_variables, body_end_variables, else_end_variables
        )

        result = self._wrap_branches_in_implies(test, body_constraints, else_constraints)
        path_result = self._wrap_branches_in_implies(
            test, path_body_constraints, path_else_constraints
        )
        return result, path_result, merge_constraints

    def _merge_branch_variables(
        self,
        test: Any,
        pre_if_variables: dict[str, Any],
        body_end_variables: dict[str, Any],
        else_end_variables: dict[str, Any],
    ) -> list[Any]:
        """Build phi-merge constraints for every variable touched by either branch.

        Mutates self.variables in place (via _new_binding) to point each
        touched name at its new merged version, so code after the if/else
        refers to the right value regardless of which branch actually ran.
        """
        touched_names = {
            name
            for name in set(body_end_variables) | set(else_end_variables)
            if body_end_variables.get(name) is not else_end_variables.get(name)
        }

        merge_constraints: list[Any] = []
        for name in touched_names:
            then_val = body_end_variables.get(name, pre_if_variables.get(name))
            else_val = else_end_variables.get(name, pre_if_variables.get(name))

            if then_val is None or else_val is None:
                # Assigned in exactly one branch with no prior binding --
                # no sound merge value exists for the path that never
                # defines it. Best effort: keep whichever binding exists,
                # same degraded (but not crashing) behavior as before this
                # fix for this specific edge case.
                self.variables[name] = then_val if then_val is not None else else_val
                continue

            var_type = self._variable_types.get(name, "int")
            merged_var = self._new_binding(name, var_type)
            merge_constraints.append(merged_var == If(test, then_val, else_val))

        return merge_constraints

    def _wrap_branches_in_implies(
        self, test: Any, body_constraints: list[Any], else_constraints: list[Any]
    ) -> Optional[Any]:
        """Shared Implies-wrapping logic for _process_if's two parallel
        (full and path-only) constraint accumulations."""
        if not body_constraints and not else_constraints:
            return None

        result = None
        if body_constraints:
            body_constraint = (
                And(body_constraints) if len(body_constraints) > 1 else body_constraints[0]
            )
            result = Implies(test, body_constraint)

        if else_constraints:
            else_constraint = (
                And(else_constraints) if len(else_constraints) > 1 else else_constraints[0]
            )
            else_implication = Implies(Not(test), else_constraint)
            result = And(result, else_implication) if result is not None else else_implication

        return result

    def _process_for(
        self,
        node: ast.For,
        collector: Optional[list[Any]] = None,
        path_collector: Optional[list[Any]] = None,
        branch_assumptions: Optional[list[Any]] = None,
    ) -> None:
        """Process a for loop with range-bounded iteration.

        This does not perform loop induction (Z3 can't do that automatically —
        see ADR-0001); it introduces the loop variable's bound as a constraint
        and processes the body once, representing "some iteration" rather than
        proving a property for all iterations. Loops without explicit invariants
        remain a documented soundness simplification, not a crash risk.

        Args:
            collector: Passed through so a loop nested inside an if-branch is
                scoped to that branch's constraint list instead of leaking
                into the unconditional global constraint set.
            path_collector: Parallel to `collector` for path_constraints (ADR-0002).
            branch_assumptions: Active branch/loop conditions so far (ADR-0002);
                the loop's own bounds are added for the body's asserts.
        """
        target_list = collector if collector is not None else self.constraints
        path_target_list = path_collector if path_collector is not None else self.path_constraints
        branch_assumptions = branch_assumptions or []

        if not isinstance(node.target, ast.Name):
            self._mark_non_verifiable(node, "Non-simple loop target")
            return

        target_var = node.target.id

        if not isinstance(node.iter, ast.Call):
            self._mark_non_verifiable(node, "Non-range loop")
            return
        if not (isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range"):
            self._mark_non_verifiable(node, "Non-range loop")
            return

        args = node.iter.args
        if len(args) == 1:
            upper = self._convert_expr(args[0])
            lower = IntVal(0)
        elif len(args) == 2:
            lower = self._convert_expr(args[0])
            upper = self._convert_expr(args[1])
        else:
            self._mark_non_verifiable(node, "range() with step not supported")
            return

        loop_var = self._new_binding(target_var, "int")
        loop_bounds = And(loop_var >= lower, loop_var < upper)
        target_list.append(loop_bounds)
        path_target_list.append(loop_bounds)

        self._process_statements(
            node.body,
            collector=collector,
            path_collector=path_collector,
            branch_assumptions=branch_assumptions + [loop_bounds],
        )

        logger.debug(f"Processed for loop with bounds: {lower} <= {target_var} < {upper}")

    def _convert_expr(self, node: ast.expr) -> Any:
        """Convert AST expression to Z3 expression."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return node.value  # bool is a subclass of int; check first
            elif isinstance(node.value, int):
                return IntVal(node.value)
            elif isinstance(node.value, float):
                return RealVal(node.value)
            else:
                raise VerifiableSubsetViolation(f"Unsupported constant: {node.value!r}")

        elif isinstance(node, ast.Name):
            if node.id not in self.variables:
                raise VerifiableSubsetViolation(f"Variable {node.id} not defined")
            return self.variables[node.id]

        elif isinstance(node, ast.BinOp):
            left = self._convert_expr(node.left)
            right = self._convert_expr(node.right)

            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.FloorDiv):
                return left / right  # Z3 handles integer division
            elif isinstance(node.op, ast.Mod):
                return left % right
            else:
                raise VerifiableSubsetViolation(f"Binary op {type(node.op)} not supported")

        elif isinstance(node, ast.Compare):
            left = self._convert_expr(node.left)
            result = None

            for op, comparator in zip(node.ops, node.comparators):
                right = self._convert_expr(comparator)

                if isinstance(op, ast.Eq):
                    constraint = left == right
                elif isinstance(op, ast.NotEq):
                    constraint = left != right
                elif isinstance(op, ast.Lt):
                    constraint = left < right
                elif isinstance(op, ast.LtE):
                    constraint = left <= right
                elif isinstance(op, ast.Gt):
                    constraint = left > right
                elif isinstance(op, ast.GtE):
                    constraint = left >= right
                else:
                    raise VerifiableSubsetViolation(f"Comparison {type(op)} not supported")

                result = constraint if result is None else And(result, constraint)
                left = right

            return result

        elif isinstance(node, ast.BoolOp):
            operands = [self._convert_expr(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return And(operands)
            elif isinstance(node.op, ast.Or):
                return Or(operands)
            else:
                raise VerifiableSubsetViolation(f"BoolOp {type(node.op)} not supported")

        elif isinstance(node, ast.UnaryOp):
            operand = self._convert_expr(node.operand)
            if isinstance(node.op, ast.Not):
                return Not(operand)
            elif isinstance(node.op, ast.USub):
                return -operand
            else:
                raise VerifiableSubsetViolation(f"UnaryOp {type(node.op)} not supported")

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if func_name in self.VERIFIABLE_BUILTINS:
                    return self._convert_builtin_call(func_name, node.args)
                else:
                    raise VerifiableSubsetViolation(f"Function call to {func_name} not verifiable")
            else:
                raise VerifiableSubsetViolation("Method calls not verifiable")

        else:
            raise VerifiableSubsetViolation(f"Expression type {type(node)} not supported")

    def _convert_builtin_call(self, func_name: str, args: list[ast.expr]) -> Any:
        """Convert built-in function call to Z3."""
        if func_name == "len":
            # len(x) - create a read-only symbolic length variable len_<name>
            if len(args) != 1:
                raise VerifiableSubsetViolation("len() takes exactly 1 argument")

            arg = args[0]
            if isinstance(arg, ast.Name):
                len_var_name = f"len_{arg.id}"
                if len_var_name not in self.variables:
                    len_var = Int(len_var_name)
                    self.variables[len_var_name] = len_var
                    self.constraints.append(len_var > 0)
                return self.variables[len_var_name]
            else:
                raise VerifiableSubsetViolation("len() on complex expressions not supported")

        elif func_name == "abs":
            if len(args) != 1:
                raise VerifiableSubsetViolation("abs() takes 1 argument")
            arg = self._convert_expr(args[0])
            return If(arg >= 0, arg, -arg)

        elif func_name == "min":
            if len(args) != 2:
                raise VerifiableSubsetViolation("min() takes 2 arguments")
            left = self._convert_expr(args[0])
            right = self._convert_expr(args[1])
            return If(left <= right, left, right)

        elif func_name == "max":
            if len(args) != 2:
                raise VerifiableSubsetViolation("max() takes 2 arguments")
            left = self._convert_expr(args[0])
            right = self._convert_expr(args[1])
            return If(left >= right, left, right)

        elif func_name in ("int", "bool"):
            if len(args) != 1:
                raise VerifiableSubsetViolation(f"{func_name}() takes 1 argument")
            return self._convert_expr(args[0])

        else:
            raise VerifiableSubsetViolation(f"Builtin {func_name} not supported")

    def _infer_type(self, node: ast.expr) -> str:
        """Infer variable type from expression."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, int):
                return "int"
            elif isinstance(node.value, float):
                return "float"

        elif isinstance(node, ast.Name):
            if node.id in self._variable_types:
                return self._variable_types[node.id]

        elif isinstance(node, (ast.Compare, ast.BoolOp)):
            return "bool"

        return "int"  # Default

    def _extract_docstring_spec(self, docstring: str, keyword: str) -> Optional[str]:
        """Extract PRE/POST/INV spec from docstring."""
        for line in docstring.split("\n"):
            if keyword in line:
                return line.split(":", 1)[1].strip()
        return None

    def _mark_non_verifiable(self, node: ast.AST, reason: str = "") -> None:
        """Mark a node as non-verifiable."""
        lineno = getattr(node, "lineno", 0)
        node_type = type(node).__name__
        self.non_verifiable_nodes.append(
            {
                "line": lineno,
                "type": node_type,
                "reason": reason,
            }
        )
        logger.debug(f"Line {lineno}: {node_type} not verifiable ({reason})")
