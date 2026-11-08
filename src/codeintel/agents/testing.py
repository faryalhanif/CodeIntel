"""Testing agent: finds untested public functions and writes pytest *templates* (never into the repo).
Generated code is syntax-checked with `ast.parse` before it is returned. Nothing is executed."""
from __future__ import annotations

import ast
import re
from collections import defaultdict

from ..models import AgentResult, Finding, Severity, SymbolInfo
from .base import AgentContext, BaseAgent

EDGE = {
    "int": ["0", "-1", "2**31"], "float": ["0.0", "-1.0", "float('inf')"], "str": ['""', '" "', '"x" * 10_000'],
    "bool": ["True", "False"], "list": ["[]", "[None]"], "dict": ["{}"], "bytes": ['b""'], "set": ["set()"],
}


def _edge_values(sym: SymbolInfo, param: str) -> list[str]:
    ann = sym.param_annotations.get(param, "")
    base = re.split(r"[\[\|\s]", ann.replace("Optional[", "").replace("typing.", ""))[0].lower()
    vals = list(EDGE.get(base, ["None", '"sample"', "0"]))
    if "Optional" in ann or "None" in ann or not ann:
        vals.append("None")
    return list(dict.fromkeys(vals))


def _module_path(file: str) -> str:
    mod = file[:-3].replace("/", ".")
    for pre in ("src.",):
        mod = mod.removeprefix(pre)
    return mod.removesuffix(".__init__")


def generate_test_module(file: str, syms: list[SymbolInfo]) -> str:
    mod = _module_path(file)
    out = ['"""Auto-generated test TEMPLATES - review, fill in expected values, then remove the skip markers."""',
           "import pytest", "", f"mod = pytest.importorskip({mod!r}, reason='module not importable in this environment')", ""]
    for s in syms:
        cases = [f"({', '.join(_edge_values(s, p)[i % len(_edge_values(s, p))] for p in s.params)})" for i in range(3)] if s.params else ["()"]
        cases = list(dict.fromkeys(cases))
        argnames = ", ".join(s.params) if s.params else ""
        out += ["",
                '@pytest.mark.skip(reason="generated template: define expected behaviour")']
        if s.params:
            out.append(f"@pytest.mark.parametrize({argnames!r}, [{', '.join(cases)}])" if len(s.params) > 1 else
                       f"@pytest.mark.parametrize({argnames!r}, [{', '.join(c.strip('()').rstrip(',') for c in cases)}])")
        out.append(f"def test_{s.name}_edge_cases({argnames}):")
        out.append(f"    # {s.signature}")
        if s.docstring:
            out.append(f"    # doc: {s.docstring.splitlines()[0][:100]}")
        call = f"mod.{s.name}({', '.join(s.params)})"
        out += [f"    result = {call}", "    # TODO: assert on result, or use pytest.raises(...) if invalid input should fail",
                "    assert result is not None or result is None"]
    code = "\n".join(out) + "\n"
    ast.parse(code)       # validate before returning
    return code


class TestingAgent(BaseAgent):
    name = "testing"
    role = "Find functions with no tests and generate pytest templates and edge cases."
    tools = ("symbol_index", "test_reference_scan")

    def run(self, ctx: AgentContext) -> AgentResult:
        test_text = "\n".join(ctx.text(p) for p in ctx.files(tests=True))
        tested = set(re.findall(r"[A-Za-z_]\w*", test_text))
        untested = [s for s in ctx.index.symbols
                    if s.kind == "function" and s.is_public and s.file.endswith(".py") and not s.name.startswith("test_")
                    and s.name not in tested and not any(x.is_test for x in ctx.index.files if x.path == s.file)]
        total = [s for s in ctx.index.symbols if s.kind == "function" and s.is_public and s.file.endswith(".py")]
        untested.sort(key=lambda s: -(s.line_end - s.line_start))
        by_file: dict[str, list[SymbolInfo]] = defaultdict(list)
        worthwhile = [s for s in untested if s.line_end - s.line_start >= 2]   # skip one-liners for templates
        for s in worthwhile[:60]:
            by_file[s.file].append(s)
        artifacts, warnings = {}, []
        for file, syms in by_file.items():
            try:
                artifacts[f"tests/test_{file[:-3].replace('/', '_')}.py"] = generate_test_module(file, syms)
            except SyntaxError as e:
                warnings.append(f"Skipped test template for {file}: {e}")
        findings = [Finding(agent=self.name, category="testing.untested_function", severity=Severity.info,
                            title=f"No test references `{s.qualname}`", file=s.file, line_start=s.line_start, line_end=s.line_end,
                            description="No test file mentions this function by name (name-based heuristic, not coverage).",
                            evidence=s.signature, suggestion="See generated template in the Testing artifacts.",
                            source="heuristic", confidence=0.6, tool="ast") for s in worthwhile[:40]]
        has_tests = bool(ctx.files(tests=True))
        pct = 100 * (1 - len(untested) / len(total)) if total else 100.0
        if not has_tests:
            pct = 0.0
        return AgentResult(agent=self.name, findings=findings, artifacts=artifacts, warnings=warnings,
                           data={"public_functions": len(total), "untested": len(untested), "name_reference_coverage_pct": round(pct, 1)},
                           summary=("This repository has no test files: " if not has_tests else "")
                           + f"{len(untested)}/{len(total)} public functions are never referenced by a test "
                           f"(~{pct:.0f}% name-level coverage; run coverage.py for real numbers).")