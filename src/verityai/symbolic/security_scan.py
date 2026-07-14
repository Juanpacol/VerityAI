"""AST-based scanner for dangerous code patterns in LLM-generated output.

Z3 verification (ast_to_smt.py / verify.py) checks whether a snippet's own
assertions are internally consistent -- it has no opinion on whether the
code does something dangerous (RCE, exfiltration, destructive filesystem/
network/process calls). This scanner is a separate, deliberately blunt
safety net: a blocklist of constructs that should never appear in code
VerityAI hands back to a caller, regardless of what Z3 says about it.

Wired into verify_python_snippet (see verify.py) rather than only the
Orchestrator's retry loop, so every caller -- refinement's incremental
re-verification, rule_validation's Z3 gate for candidate KG rules, the
evaluation baselines -- gets this safety net for free. A "corrected_code"
rule candidate containing os.system() is exactly as dangerous as LLM
output containing it.
"""

import ast
from dataclasses import dataclass

# Modules whose mere import is treated as dangerous -- these grant broad
# system/process/network access no verified-code snippet should need.
DANGEROUS_MODULES = frozenset(
    {
        "os", "subprocess", "socket", "ctypes", "shutil", "sys",
        "pickle", "marshal", "pty", "telnetlib", "ftplib", "paramiko",
        "multiprocessing",
    }
)

# Builtins that can execute arbitrary code even without importing one of
# the modules above.
DANGEROUS_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__", "globals", "locals"})

# (module_alias, attribute) pairs that execute commands/arbitrary code --
# checked separately from DANGEROUS_MODULES so `import os` is flagged even
# on the import line itself, and `os.system(...)` is flagged as a call too
# (belt and suspenders: either detector alone would already catch this
# example, but a module imported under an alias, e.g. `import os as o`,
# only trips the call-based check since the alias name won't match).
DANGEROUS_ATTR_CALLS = frozenset(
    {
        ("os", "system"), ("os", "popen"), ("os", "exec"), ("os", "execve"),
        ("os", "spawn"), ("os", "spawnl"), ("os", "spawnv"),
        ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "Popen"),
        ("subprocess", "check_output"), ("subprocess", "check_call"),
        ("pickle", "loads"), ("pickle", "load"),
        ("socket", "socket"),
        ("shutil", "rmtree"),
    }
)


@dataclass(frozen=True)
class SecurityFinding:
    """One dangerous construct found in a code snippet."""
    line: int
    construct: str
    description: str


def scan_for_dangerous_patterns(code: str) -> list[SecurityFinding]:
    """Scan Python source for known-dangerous constructs.

    Returns an empty list for clean code. Never raises on a syntax error --
    returns a single finding describing the parse failure instead, since
    code that can't even be parsed shouldn't be silently treated as "safe."
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [SecurityFinding(line=e.lineno or 0, construct="syntax_error", description=str(e))]

    findings: list[SecurityFinding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in DANGEROUS_MODULES:
                    findings.append(
                        SecurityFinding(
                            line=node.lineno,
                            construct=f"import {alias.name}",
                            description=f"Importing '{alias.name}' grants broad system/process access",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            root_module = (node.module or "").split(".")[0]
            if root_module in DANGEROUS_MODULES:
                findings.append(
                    SecurityFinding(
                        line=node.lineno,
                        construct=f"from {node.module} import ...",
                        description=f"Importing from '{node.module}' grants broad system/process access",
                    )
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DANGEROUS_CALL_NAMES:
                findings.append(
                    SecurityFinding(
                        line=node.lineno,
                        construct=f"{func.id}(...)",
                        description=f"'{func.id}' can execute arbitrary code",
                    )
                )
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                if pair in DANGEROUS_ATTR_CALLS:
                    findings.append(
                        SecurityFinding(
                            line=node.lineno,
                            construct=f"{pair[0]}.{pair[1]}(...)",
                            description=f"'{pair[0]}.{pair[1]}' can execute commands or arbitrary code",
                        )
                    )

    return findings
