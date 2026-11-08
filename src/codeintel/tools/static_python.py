"""Deterministic AST checks for Python. Findings here are `heuristic` (pattern matched), not LLM guesses."""
from __future__ import annotations

import ast
import hashlib
import re

from ..models import Finding, Severity

RISKY_CALLS = {"open", "int", "float", "json.loads", "json.load", "requests.get", "requests.post", "requests.put",
               "requests.delete", "os.remove", "os.makedirs", "shutil.copy", "shutil.rmtree", "subprocess.run",
               "datetime.strptime", "yaml.safe_load", "pickle.load", "pickle.loads"}
SECRET_NAME = re.compile(r"(pass(word|wd)?|secret|api[_-]?key|token|private[_-]?key|auth)", re.I)
PLACEHOLDER = re.compile(r"^(|changeme|change_me|xxx+|your[_-].*|<.*>|\$\{.*\}|none|null|todo|example.*)$", re.I)


def _snip(lines: list[str], a: int, b: int | None = None) -> str:
    b = b or a
    return "\n".join(lines[max(a - 1, 0):min(b, len(lines))])[:600]


def _name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _f(agent, cat, sev, title, desc, file, node, lines, sug="", conf=0.75, notes=None) -> Finding:
    a = getattr(node, "lineno", 1)
    b = getattr(node, "end_lineno", a) or a
    return Finding(agent=agent, category=cat, severity=sev, title=title, description=desc, file=file,
                   line_start=a, line_end=b, evidence=_snip(lines, a, min(b, a + 6)), suggestion=sug,
                   source="heuristic", confidence=conf, tool="ast", notes=notes or [])


def _in_try(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    cur = parents.get(node)
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if isinstance(cur, ast.Try):
            return True
        cur = parents.get(cur)
    return False


def _is_sqlish(node: ast.AST) -> bool:
    """Is this expression a dynamically built string (f-string, %, +, .format)?"""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return any(isinstance(s, ast.Constant) and isinstance(s.value, str) for s in (node.left, node.right)) or \
               _is_sqlish(node.left) or _is_sqlish(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def bug_checks(path: str, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [Finding(agent="bug", category="bug.syntax_error", severity=Severity.high, title="Syntax error",
                        description=str(e), file=path, line_start=e.lineno or 0, line_end=e.lineno or 0,
                        source="static_tool", confirmed=True, confidence=1.0, tool="ast")]
    lines = text.splitlines()
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                out.append(_f("bug", "bug.bare_except", Severity.medium, "Bare `except:` swallows every error",
                              "Catches SystemExit/KeyboardInterrupt and hides real failures.", path, node, lines,
                              "Catch specific exceptions and log them."))
            if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                out.append(_f("bug", "bug.swallowed_exception", Severity.medium, "Exception silently ignored",
                              "The handler does nothing, so failures vanish without trace.", path, node, lines,
                              "Log the exception or re-raise; handle it deliberately."))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in [*node.args.defaults, *node.args.kw_defaults]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append(_f("bug", "bug.mutable_default", Severity.medium,
                                  f"Mutable default argument in `{node.name}`",
                                  "The default object is created once and shared across calls.", path, node, lines,
                                  "Use `None` as default and create the object inside the function."))
            risky = [n for n in ast.walk(node) if isinstance(n, ast.Call) and _name(n.func) in RISKY_CALLS
                     and not _in_try(n, parents)]
            if risky and not node.name.startswith("_"):
                names = sorted({_name(n.func) for n in risky})
                out.append(_f("bug", "bug.unhandled_exception", Severity.low,
                              f"`{node.name}` calls fallible operations without error handling",
                              f"Calls {', '.join(names)} which can raise (I/O, parsing, network) with no try/except "
                              "in this function. Callers may or may not handle it.", path, risky[0], lines,
                              "Decide where the error should be handled; add handling or document that it propagates.",
                              conf=0.45, notes=["Potential issue: verify callers before acting."]))
        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant) and comp.value is None:
                    out.append(_f("bug", "bug.none_comparison", Severity.low, "Comparison to None with ==/!=",
                                  "Use `is None` / `is not None`.", path, node, lines, "Replace with identity check."))
        elif isinstance(node, ast.Assert):
            out.append(_f("bug", "bug.assert_validation", Severity.info, "`assert` used in application code",
                          "Asserts are stripped under `python -O`; do not use them for input validation.", path,
                          node, lines, "Raise an explicit exception.", conf=0.4))
    return out


def duplicate_functions(files: dict[str, str], min_stmts: int = 6) -> list[Finding]:
    """Detect structurally identical functions (names/constants normalised away)."""
    class Norm(ast.NodeTransformer):
        def visit_Name(self, n):
            return ast.copy_location(ast.Name(id="_", ctx=n.ctx), n)

        def visit_arg(self, n):
            return ast.arg(arg="_", annotation=None)

        def visit_Constant(self, n):
            return ast.copy_location(ast.Constant(value=type(n.value).__name__), n)

        def visit_FunctionDef(self, n):
            n.name = "_"
            n.decorator_list = []
            self.generic_visit(n)
            return n

    seen: dict[str, tuple[str, str, int]] = {}
    out: list[Finding] = []
    for path, text in files.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if sum(1 for _ in ast.walk(fn) if isinstance(_, ast.stmt)) < min_stmts:
                continue
            import copy
            body = Norm().visit(copy.deepcopy(fn))
            h = hashlib.sha1(ast.dump(body).encode()).hexdigest()
            if h in seen:
                p0, n0, l0 = seen[h]
                out.append(Finding(agent="bug", category="bug.duplicate_code", severity=Severity.low,
                                   title=f"`{fn.name}` duplicates `{n0}` ({p0}:{l0})",
                                   description="Structurally identical function bodies; fixes must be applied twice.",
                                   file=path, line_start=fn.lineno, line_end=fn.end_lineno or fn.lineno,
                                   evidence=_snip(lines, fn.lineno, fn.lineno + 4), source="heuristic", tool="ast",
                                   suggestion="Extract the shared logic into one function.", confidence=0.8))
            else:
                seen[h] = (path, fn.name, fn.lineno)
    return out


def security_checks(path: str, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    out: list[Finding] = []
    S = "security"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = _name(node.func)
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            if fn in ("eval", "exec"):
                out.append(_f(S, "security.code_execution", Severity.high, f"Use of `{fn}`",
                              "Executes arbitrary code; exploitable if any input reaches it.", path, node, lines,
                              "Use `ast.literal_eval` or an explicit dispatch table.", 0.8))
            elif fn.startswith("subprocess.") or fn in ("os.system", "os.popen"):
                shell = kw.get("shell")
                if fn in ("os.system", "os.popen") or (isinstance(shell, ast.Constant) and shell.value is True):
                    out.append(_f(S, "security.command_injection", Severity.high, "Shell command execution",
                                  "Commands run through a shell are injectable when built from variable input.",
                                  path, node, lines, "Pass an argument list with shell=False.", 0.7))
            elif fn in ("pickle.load", "pickle.loads", "marshal.loads", "shelve.open"):
                out.append(_f(S, "security.unsafe_deserialization", Severity.high, f"`{fn}` on possibly untrusted data",
                              "Unpickling attacker-controlled data gives code execution.", path, node, lines,
                              "Use JSON or another safe format.", 0.65))
            elif fn == "yaml.load" and "Loader" not in kw:
                out.append(_f(S, "security.unsafe_yaml", Severity.high, "`yaml.load` without a safe Loader",
                              "Can construct arbitrary Python objects.", path, node, lines, "Use `yaml.safe_load`.", 0.85))
            elif fn in ("hashlib.md5", "hashlib.sha1"):
                out.append(_f(S, "security.weak_hash", Severity.medium, f"Weak hash `{fn}`",
                              "MD5/SHA-1 are broken for security purposes (passwords, signatures).", path, node, lines,
                              "Use SHA-256+, and bcrypt/argon2 for passwords.", 0.6))
            elif fn.startswith("requests.") and isinstance(kw.get("verify"), ast.Constant) and kw["verify"].value is False:
                out.append(_f(S, "security.tls_verification_disabled", Severity.high, "TLS verification disabled",
                              "`verify=False` allows man-in-the-middle attacks.", path, node, lines,
                              "Remove `verify=False`; trust a proper CA bundle.", 0.9))
            elif fn.endswith((".execute", ".executemany", ".raw")) and node.args and _is_sqlish(node.args[0]):
                out.append(_f(S, "security.sql_injection", Severity.high, "SQL built with string formatting",
                              "Interpolating values into SQL lets input change the query.", path, node, lines,
                              "Use parameterised queries: `cursor.execute(sql, params)`.", 0.8))
            elif fn == "tempfile.mktemp":
                out.append(_f(S, "security.insecure_tempfile", Severity.medium, "`tempfile.mktemp` is race-prone",
                              "Use `NamedTemporaryFile` or `mkstemp`.", path, node, lines, "Switch to mkstemp.", 0.8))
            elif fn.endswith(".run") and "debug" in kw and isinstance(kw["debug"], ast.Constant) and kw["debug"].value is True:
                out.append(_f(S, "security.debug_enabled", Severity.medium, "Debug mode enabled",
                              "Flask/other debug servers expose an interactive console.", path, node, lines,
                              "Disable debug outside local development.", 0.8))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                nm = (t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute)
                      else t.slice.value if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                      and isinstance(t.slice.value, str) else "")
                val = node.value.value
                if nm and SECRET_NAME.search(nm) and len(val) >= 6 and not PLACEHOLDER.match(val) and " " not in val:
                    out.append(_f(S, "security.hardcoded_secret", Severity.high, f"Hardcoded credential in `{nm}`",
                                  "Secrets in source control are exposed to everyone with repo access.", path, node,
                                  lines, "Load from environment or a secrets manager and rotate this value.", 0.7))
    return out