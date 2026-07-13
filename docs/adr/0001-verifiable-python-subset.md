# ADR-0001: Verifiable Python Subset Definition

**Date**: 2026-07-13  
**Status**: ACCEPTED  
**Author**: Juan Pablo Botero Espinosa  
**Scope**: Phase 1 (Z3 AST converter scope)

## Problem

The AST-to-Z3 converter is the most complex component of VerityAI. Z3 Theorem Prover
has fundamental limitations:

- **Loops**: Cannot automatically infer loop invariants (requires explicit specification)
- **Recursion**: Requires manual unrolling or explicit induction (not automatic)
- **Arrays**: Mutable array operations need complex path-sensitive analysis
- **Exceptions**: Control flow via exceptions complicates path condition construction
- **Timeouts**: Z3 returns `unknown` when it can't solve in time (20% expected rate)

Without a clear scope, the converter becomes a never-ending source of scope creep.
This ADR defines the boundary: what we **can** verify (verifiable subset) vs. what we
**cannot** (marked as "not verified", not "verification failed").

## Decision

VerityAI initially supports verification of a **restricted subset of Python**:

### ✅ VERIFIABLE Constructs

#### Data Types
- `int` (unlimited precision, but bounded in specs)
- `bool` (true/false)
- `float` (via real theory, limited)
- `list[T]` where T is int/bool (fixed-size, bounded)
- `dict[str, int]` (key-value, fixed-size, bounded)

#### Control Flow
- `if`/`elif`/`else` (full support)
- `for` loops **with explicit invariant required** (see below)
- `while` loops **with explicit invariant required**
- Sequential statements (no implicit dependencies)

#### Expressions
- Arithmetic: `+`, `-`, `*`, `//` (integer division), `%`
- Comparison: `<`, `<=`, `>`, `>=`, `==`, `!=`
- Logical: `and`, `or`, `not`
- Assignment: simple assignment only (no multiple assignment)

#### Assertions & Specifications
- `assert condition` → converted to Z3 constraint
- Docstring annotations (Hoare triples):
  ```python
  def binary_search(arr, target):
      """
      PRE: len(arr) > 0 and arr is sorted
      POST: result == -1 or arr[result] == target
      INV: 0 <= left <= right <= len(arr)
      """
  ```

### ❌ NOT VERIFIABLE (Degraded Mode)

These constructs are **allowed in the code** but are **marked as "not verified"**:

- **Recursion** (requires manual unrolling)
- **Function calls** (except built-ins, see below)
- **Exceptions** (`try`/`except`/`finally`)
- **Classes & methods** (OOP features)
- **Generators** (`yield`)
- **Comprehensions** (list/dict/set)
- **Context managers** (`with` statement)
- **Decorators**
- **Import statements**
- **Mutable operations on strings** (strings are immutable, but `.replace()` etc. are not verified)
- **Nested data structures** (list of dicts, dict of lists)

These constructs are **silently marked as "NOT_VERIFIED"** with explicit explanation:
```
Code contains non-verifiable constructs:
- Line 7: Function call to user_defined_sort() [SKIP]
- Line 12: Exception handling [SKIP]
Verification status: PARTIAL (core algorithm verified, helpers not)
```

### Built-in Functions (Allowed)

Verifiable built-ins:
- `len(x)` → converted to array length
- `range(a, b)` → loop bounds
- `abs(x)` → Z3 abs function
- `min(a, b)`, `max(a, b)` → Z3 min/max
- `enumerate(arr)` → pair (index, value)

**NOT verifiable** (but allowed):
- `sorted()`, `reversed()` → data transformation (skip verification)
- `sum()`, `any()`, `all()` → reductions (partial support if loop-free)
- I/O: `print()`, `input()` → not verified
- String operations: `.split()`, `.join()` → not verified

---

## Scope Examples

### ✅ VERIFIABLE: Binary Search

```python
def binary_search(arr: list[int], target: int) -> int:
    """
    PRE: len(arr) > 0 and arr is sorted ascending
    POST: return -1 if not found, else index where arr[index] == target
    INV: 0 <= left <= right <= len(arr)  # Explicit loop invariant
    """
    left, right = 0, len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
```

**Why it's verifiable**:
- Only int/bool operations
- Loop has explicit invariant in docstring
- No function calls, exceptions, or OOP
- All conditions are linear predicates

### ⚠️ PARTIAL: Linear Search with Early Exit (Exception)

```python
def find_first_even(arr: list[int]) -> int:
    """
    PRE: len(arr) > 0
    POST: return index of first even number, or -1 if none
    """
    for i in range(len(arr)):
        if arr[i] % 2 == 0:
            return i  # Early exit (OK)
    return -1
```

**Verification**:
- ✅ Loop + condition verifiable
- ❌ Function uses early exit (not strictly a loop invariant structure)
- **Result**: PARTIAL (core logic verified, but completeness proof weak)

### ❌ NOT VERIFIABLE: Recursive Sort

```python
def quicksort(arr: list[int]) -> list[int]:
    """
    Quick sort via recursion
    """
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    less = [x for x in arr if x < pivot]      # ❌ Comprehension
    equal = [x for x in arr if x == pivot]    # ❌ Comprehension
    greater = [x for x in arr if x > pivot]   # ❌ Comprehension
    return quicksort(less) + equal + quicksort(greater)  # ❌ Recursion
```

**Verification Status**: NOT_VERIFIED
- Line 4: List comprehension (non-verifiable)
- Line 7: Recursive call (non-verifiable)
- Line 8: List concatenation (non-verifiable)
- **Recommendation**: Use iterative binary search instead

---

## Degradation Strategy

When code contains non-verifiable constructs:

```python
VerificationResult(
    status=VerificationStatus.PARTIAL,
    confidence=0.65,  # Lower confidence
    violations=[],    # No violations found in verifiable subset
    metadata={
        "verifiable_lines": [1, 2, 3, 4, 5],
        "non_verifiable_lines": [7, 12],
        "non_verifiable_constructs": [
            {"line": 7, "construct": "function_call", "name": "sort()"},
            {"line": 12, "construct": "exception_handler", "type": "try/except"}
        ],
        "message": "Core algorithm verified. Helpers/error handling not verified."
    }
)
```

**User-Facing Explanation**:
```
✓ Verification Status: PARTIAL
Confidence: 65%

The core sorting logic was formally verified, but:
- Line 7: Uses built-in sort() [helper function, not verified]
- Line 12: Exception handling [edge cases, not verified]

Recommendation: For production use, consider verifying edge cases manually.
```

---

## Implementation Notes

### For AST Converter (`symbolic/ast_to_smt.py`)

1. **Whitelist Approach**: Only convert constructs in the verifiable subset
2. **Marking**: When encountering non-verifiable code:
   ```python
   # In ast_to_smt.py
   if node is FunctionCall and node.func not in BUILTIN_VERIFIABLE:
       self.non_verifiable_nodes.append((node.lineno, "function_call", node.func))
       return SkipNode()  # Don't convert
   ```

3. **Confidence Reduction**: Confidence score is reduced proportionally:
   ```python
   confidence = 1.0 * (verifiable_fraction) + 0.5 * (non_verifiable_fraction)
   ```

### For KG (Knowledge Graph)

- Store algorithms with metadata: `verified_constructs`, `non_verified_constructs`
- Example pattern node:
  ```json
  {
    "name": "binary_search",
    "verified_constructs": ["loops", "conditionals", "arithmetic"],
    "non_verified_constructs": [],
    "verification_status": "FULL"
  }
  ```

### For Rule Engine

- Security rules only apply to verifiable subset
- Example: "SQL injection prevention" only checks string operations
  - ✓ Verifies: string literals
  - ✗ Doesn't verify: function call `.format()` result

---

## Rationale

### Why restrict to this subset?

1. **Z3 Limitations**: Loop invariants, recursion, exceptions are fundamentally hard
2. **Realistic Scope**: 80% of algorithms can be expressed in this subset
3. **Clear Boundaries**: No silent failures or "kinda verified" results
4. **Degradation**: Non-verifiable code gets explicit confidence reduction
5. **Future Expansion**: Can add support for more constructs later (research project)

### Why not use SMT solver with induction?

- **Undecidable**: Full induction is undecidable for general programs
- **Complexity**: Would require interactive theorem proving (outside scope)
- **Time**: Would break latency requirements (seconds, not minutes)

### Why allow non-verifiable code at all?

- **Pragmatism**: Real code uses exceptions, helper functions, recursion
- **Transparency**: Better to mark "PARTIAL" than reject the code
- **Enterprise**: Users want explanations, not rejections
- **Learning**: Non-verified parts can be iteratively refined

---

## Changes to Ontology

The `VerificationResult` model gains a new field:

```python
class VerificationResult(BaseModel):
    status: VerificationStatus  # PASS, FAIL, PARTIAL, UNKNOWN, NOT_VERIFIED
    verifiable_fraction: float  # 0.0-1.0, what % of code was verifiable
    non_verifiable_nodes: list[dict]  # Lines + constructs not verified
    confidence: float  # Adjusted for non-verifiable parts
```

---

## Acceptance Criteria

Phase 1 Semana 1 is done when:

1. ✅ This ADR is documented and approved
2. ✅ Z3 wrapper (`z3_engine.py`) handles sat/unsat/unknown/timeout
3. ✅ AST converter (`ast_to_smt.py`) implements verifiable subset
4. ✅ At least 3 test cases pass verification (1 algorithm + 2 security rules)
5. ✅ 1 test case fails verification and confidence is lowered appropriately
6. ✅ Non-verifiable constructs are detected and marked in output

---

## References

- Z3 Documentation: https://z3prover.github.io/
- SMT-LIB2 Standard: http://www.smtlib.org/
- Hoare Logic: https://en.wikipedia.org/wiki/Hoare_logic
- Loop Invariants: https://en.wikipedia.org/wiki/Loop_invariant

---

## Addendum (Phase 1 Week 4): SSA Versioning for Assignment Semantics

**Date**: 2026-07-13
**Trigger**: Running the converter against the full 20-algorithm seed dataset
(not just hand-picked snippets) immediately surfaced a soundness bug: the
initial implementation mapped each Python variable name to a single, reused
Z3 variable. Sequential reassignment — `x = 5; x = x + 1` — therefore produced
the constraint `x == x + 1`, which is unsatisfiable for every integer. The
converter was reporting ordinary, correct code as failing verification.

**Fix**: Assignment (`ast.Assign`) and augmented assignment (`ast.AugAssign`)
now create a **new, version-suffixed Z3 variable** on every write
(`x`, `x__v2`, `x__v3`, ...) and rebind `self.variables[name]` to the latest
version. Reads always resolve to whatever is currently bound. RHS expressions
are evaluated *before* rebinding the LHS, so `x = x + 1` correctly reads the
prior version on the right and introduces a fresh version on the left.

**Related fix, same root cause**: the original `convert_code` used
`ast.walk()` to dispatch every node in the tree, which visits statements
inside `if`/`for` bodies twice — once directly, and once again through the
parent's own explicit body-processing. This double-processing added an
**unconditional** copy of a branch's assignment constraint alongside the
correctly `Implies(test, ...)`-wrapped one, silently over-constraining
conditional code. Fixed by dispatching each statement exactly once, walking
only `tree.body` (and each function's `.body`) rather than the full tree.

**Verification**: `tests/unit/test_ast_extended.py::TestASTConverterSSASoundness`
pins both fixes down as permanent regressions, and
`tests/integration/test_week4_e2e.py` runs the converter against all 20 seed
algorithms to catch this class of bug before it reaches Phase 2.

**Lesson for the plan's own risk section**: this is precisely the risk this
ADR called out for loops/recursion — but it turned out sequential assignment
itself needed the same rigor. Confirms the "walking skeleton before scaling
seed data" sequencing decision was correct: this bug was invisible against
the 3 seed rules used in Week 1 and only surfaced once real algorithms with
multi-statement bodies were run through the pipeline in Week 4.

---

**Next**: Implement `symbolic/z3_engine.py` and `symbolic/ast_to_smt.py` based on this definition.
