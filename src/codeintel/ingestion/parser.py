"""Structure extraction. Python uses the stdlib AST (exact); other languages use a regex fallback.

Extension point: swap `_parse_generic` for tree-sitter queries to get exact multi-language parsing.
"""
from __future__ import annotations

import ast
import re

from ..models import SymbolInfo


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return ""


def _call_name(node: ast.Call) -> str:
    f = node.func
    parts: list[str] = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.symbols: list[SymbolInfo] = []
        self.imports: list[tuple[str, int, list[str]]] = []   # (module, level, names)
        self._stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.imports.append((a.name, 0, []))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append((node.module or "", node.level, [a.name for a in node.names]))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qual = ".".join([*self._stack, node.name])
        self.symbols.append(SymbolInfo(
            kind="class", name=node.name, qualname=qual, file=self.file, line_start=node.lineno,
            line_end=node.end_lineno or node.lineno, signature=f"class {node.name}({', '.join(_unparse(b) for b in node.bases)})",
            docstring=ast.get_docstring(node) or "", decorators=[_unparse(d) for d in node.decorator_list],
            is_public=not node.name.startswith("_")))
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def _func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = ".".join([*self._stack, node.name])
        in_class = bool(self._stack) and self.symbols and any(
            s.kind == "class" and s.qualname == ".".join(self._stack) for s in self.symbols)
        nested = bool(self._stack) and not in_class      # closure inside a function: not importable
        args = node.args
        allargs = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        params = [a.arg for a in allargs if a.arg not in ("self", "cls")]
        ann = {a.arg: _unparse(a.annotation) for a in allargs if a.annotation is not None}
        calls = sorted({_call_name(n) for n in ast.walk(node) if isinstance(n, ast.Call) and _call_name(n)})
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {_unparse(node.returns)}" if node.returns else ""
        self.symbols.append(SymbolInfo(
            kind="method" if in_class else "function", name=node.name, qualname=qual, file=self.file,
            line_start=node.lineno, line_end=node.end_lineno or node.lineno,
            signature=f"{prefix} {node.name}({_unparse(args)}){ret}", docstring=ast.get_docstring(node) or "",
            decorators=[_unparse(d) for d in node.decorator_list], calls=calls, params=params,
            param_annotations=ann, is_async=isinstance(node, ast.AsyncFunctionDef),
            is_public=not node.name.startswith("_") and not nested))
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _func
    visit_AsyncFunctionDef = _func


class ParsedFile:
    def __init__(self, path: str, language: str) -> None:
        self.path = path
        self.language = language
        self.symbols: list[SymbolInfo] = []
        self.imports: list[tuple[str, int, list[str]]] = []
        self.syntax_error: str = ""


_GENERIC = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_]\w*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
    ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(")),
    ("function", re.compile(r"^\s*(?:public|private|protected|static|\s)+[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws [\w, ]+)?\{")),
]
_IMPORT_GENERIC = re.compile(r"""(?:import\s+(?:.+?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""")


def _parse_generic(parsed: ParsedFile, text: str) -> None:
    lines = text.splitlines()
    starts: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, 1):
        for kind, rx in _GENERIC:
            m = rx.match(line)
            if m and m.group(1) not in {"if", "for", "while", "switch", "catch", "return"}:
                starts.append((i, kind, m.group(1)))
                break
        for m in _IMPORT_GENERIC.finditer(line):
            parsed.imports.append((m.group(1) or m.group(2), 0, []))
    for idx, (ln, kind, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        parsed.symbols.append(SymbolInfo(kind=kind, name=name, qualname=name, file=parsed.path,  # type: ignore[arg-type]
                                         line_start=ln, line_end=max(ln, end), signature=lines[ln - 1].strip()[:200]))


def parse_file(path: str, language: str, text: str) -> ParsedFile:
    parsed = ParsedFile(path, language)
    if language == "python":
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as e:
            parsed.syntax_error = f"{e.msg} (line {e.lineno})"
            return parsed
        v = _Visitor(path)
        v.visit(tree)
        parsed.symbols, parsed.imports = v.symbols, v.imports
    elif language in {"javascript", "typescript", "java", "go"}:
        _parse_generic(parsed, text)
    return parsed