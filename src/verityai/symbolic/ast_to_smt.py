"""Convert Python AST to Z3 SMT constraints (verifiable subset)."""

import ast
import logging
from typing import Any, Optional

from z3 import (
    And,
    Bool,
    Int,
    IntVal,
    If,
    Implies,
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
    VERIFIABLE_METHODS = {}  # Methods we can verify (empty for now)

    def __init__(self, allow_partial: bool = True):
        """Initialize converter.

        Args:
            allow_partial: If True, silently skip non-verifiable constructs.
                          If False, raise exception.
        """
        self.allow_partial = allow_partial
        self.variables = {}  # Map variable names to Z3 variables
        self.constraints = []  # List of Z3 constraints
        self.non_verifiable_nodes = []  # Lines that couldn't be verified

    def convert_code(self, code_str: str) -> tuple[list[Any], list[dict]]:
        """Convert Python code string to Z3 constraints.

        Args:
            code_str: Python code as string

        Returns:
            (constraints, non_verifiable_nodes)
        """
        tree = ast.parse(code_str)
        self.constraints = []
        self.variables = {}

        for node in ast.walk(tree):
            try:
                if isinstance(node, ast.FunctionDef):
                    self._process_function(node)
                elif isinstance(node, (ast.Assign, ast.AugAssign)):
                    constraint = self._process_assignment(node)
                    if constraint:
                        self.constraints.append(constraint)
                elif isinstance(node, ast.Assert):
                    constraint = self._process_assert(node)
                    if constraint:
                        self.constraints.append(constraint)
                elif isinstance(node, ast.If):
                    constraint = self._process_if(node)
                    if constraint:
                        self.constraints.append(constraint)
                elif isinstance(node, ast.For):
                    self._process_for(node)
                elif isinstance(node, (ast.While, ast.FunctionCall, ast.With, ast.Try)):
                    # Non-verifiable
                    self._mark_non_verifiable(node)
            except VerifiableSubsetViolation as e:
                if not self.allow_partial:
                    raise
                self._mark_non_verifiable(node, str(e))

        return self.constraints, self.non_verifiable_nodes

    def _process_function(self, node: ast.FunctionDef) -> None:
        """Process function definition and extract preconditions/postconditions from docstring."""
        # Parse docstring for PRE/POST/INV annotations
        docstring = ast.get_docstring(node)
        if not docstring:
            return

        # Extract PRE, POST, INV from docstring (simple parsing)
        preconditions = self._extract_docstring_spec(docstring, "PRE")
        postconditions = self._extract_docstring_spec(docstring, "POST")
        invariants = self._extract_docstring_spec(docstring, "INV")

        # TODO: Convert these to Z3 constraints based on function signature

    def _process_assignment(self, node: ast.Assign) -> Optional[Any]:
        """Convert assignment to Z3 constraint (equality)."""
        if len(node.targets) != 1:
            raise VerifiableSubsetViolation("Multiple assignment targets not supported")

        target = node.targets[0]
        value = node.value

        if isinstance(target, ast.Name):
            var_name = target.id
            # Ensure variable exists in Z3
            if var_name not in self.variables:
                # Infer type from value
                var_type = self._infer_type(value)
                if var_type == "int":
                    self.variables[var_name] = Int(var_name)
                elif var_type == "bool":
                    self.variables[var_name] = Bool(var_name)
                else:
                    raise VerifiableSubsetViolation(f"Unknown type for {var_name}")

            # Convert value to Z3 expression
            z3_value = self._convert_expr(value)
            # Return equality constraint
            return self.variables[var_name] == z3_value

        else:
            raise VerifiableSubsetViolation(f"Assignment target {type(target)} not supported")

    def _process_assert(self, node: ast.Assert) -> Optional[Any]:
        """Convert assert statement to Z3 constraint."""
        return self._convert_expr(node.test)

    def _process_if(self, node: ast.If) -> Optional[Any]:
        """Convert if statement to Z3 constraints."""
        test = self._convert_expr(node.test)

        # Process body (convert assignments in if body)
        body_constraints = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                constraint = self._process_assignment(stmt)
                if constraint:
                    body_constraints.append(constraint)

        # Process orelse (else/elif)
        else_constraints = []
        for stmt in node.orelse:
            if isinstance(stmt, ast.Assign):
                constraint = self._process_assignment(stmt)
                if constraint:
                    else_constraints.append(constraint)

        # Combine: (test IMPLIES body_constraints) AND (NOT test IMPLIES else_constraints)
        if body_constraints:
            body_constraint = And(body_constraints)
            result = Implies(test, body_constraint)

            if else_constraints:
                else_constraint = And(else_constraints)
                result = And(result, Implies(Not(test), else_constraint))

            return result
        return None

    def _process_for(self, node: ast.For) -> None:
        """Process for loop.

        Note: Requires explicit loop invariant in docstring of enclosing function.
        For now, we just mark as non-verifiable.
        """
        # TODO: Extract loop invariant and verify
        self._mark_non_verifiable(node, "Loop verification requires explicit invariant")

    def _convert_expr(self, node: ast.expr) -> Any:
        """Convert AST expression to Z3 expression."""
        if isinstance(node, ast.Constant):
            # Number literal
            if isinstance(node.value, int):
                return IntVal(node.value)
            elif isinstance(node.value, float):
                return RealVal(node.value)
            elif isinstance(node.value, bool):
                return node.value  # Z3 uses Python bool
            else:
                raise VerifiableSubsetViolation(f"Unsupported constant: {node.value}")

        elif isinstance(node, ast.Name):
            # Variable reference
            if node.id not in self.variables:
                raise VerifiableSubsetViolation(f"Variable {node.id} not defined")
            return self.variables[node.id]

        elif isinstance(node, ast.BinOp):
            # Binary operation
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
            # Comparison chain
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
            # Logical AND/OR
            operands = [self._convert_expr(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return And(operands)
            elif isinstance(node.op, ast.Or):
                return Or(operands)
            else:
                raise VerifiableSubsetViolation(f"BoolOp {type(node.op)} not supported")

        elif isinstance(node, ast.UnaryOp):
            # Unary operation
            operand = self._convert_expr(node.operand)
            if isinstance(node.op, ast.Not):
                return Not(operand)
            elif isinstance(node.op, ast.USub):
                return -operand
            else:
                raise VerifiableSubsetViolation(f"UnaryOp {type(node.op)} not supported")

        elif isinstance(node, ast.Call):
            # Function call
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
            # len(x) - not directly supported in Z3 for arrays
            # For now, return a symbolic value
            raise VerifiableSubsetViolation("len() calls need special handling in AST phase")

        elif func_name == "abs":
            if len(args) != 1:
                raise VerifiableSubsetViolation("abs() takes 1 argument")
            from z3 import If
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
            if isinstance(node.value, int):
                return "int"
            elif isinstance(node.value, bool):
                return "bool"
            elif isinstance(node.value, float):
                return "float"

        elif isinstance(node, ast.Name):
            if node.id in self.variables:
                # Infer from existing variable
                var = self.variables[node.id]
                if hasattr(var, "sort"):
                    sort_name = str(var.sort())
                    if "Int" in sort_name:
                        return "int"
                    elif "Bool" in sort_name:
                        return "bool"

        elif isinstance(node, ast.Compare):
            return "bool"

        elif isinstance(node, ast.BoolOp):
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
        self.non_verifiable_nodes.append({
            "line": lineno,
            "type": node_type,
            "reason": reason,
        })
        logger.debug(f"Line {lineno}: {node_type} not verifiable ({reason})")
