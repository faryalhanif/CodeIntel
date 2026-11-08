from __future__ import annotations

from ..models import AgentResult
from ..tools.external import run_ruff
from ..tools.static_python import bug_checks, duplicate_functions
from .base import AgentContext, BaseAgent
from .llm_review import llm_review


class BugAgent(BaseAgent):
    name = "bug"
    role = "Find likely defects: swallowed errors, unhandled exceptions, mutable defaults, duplicates, lint-level bugs."
    tools = ("ast_checks", "ruff", "duplicate_detector", "llm_review")

    def run(self, ctx: AgentContext) -> AgentResult:
        findings, warnings = [], []
        py = ctx.files("python", tests=False)
        texts = {p: ctx.text(p) for p in py}
        for p, t in texts.items():
            findings += bug_checks(p, t)
        findings += duplicate_functions(texts)
        ruff_f, w = run_ruff(ctx.root, ctx.settings)
        findings += [f for f in ruff_f if not any(x.startswith(("tests/", "test/")) for x in [f.file])]
        warnings += w
        # LLM pass: only on functions the cheap checks already flagged, longest first
        flagged = {(f.file, f.line_start) for f in findings if f.category == "bug.unhandled_exception"}
        cands = [s for s in ctx.index.symbols if s.kind in ("function", "method") and s.file in texts
                 and any(s.line_start <= ln <= s.line_end for fl, ln in flagged if fl == s.file)]
        cands.sort(key=lambda s: -(s.line_end - s.line_start))
        llm_f, w = llm_review(ctx, self.name, cands, "Look for logic errors, unhandled exceptions and None/edge-case bugs.", "bug")
        findings += llm_f
        warnings += w
        return AgentResult(agent=self.name, findings=findings, warnings=warnings,
                           summary=f"{len(findings)} potential defects across {len(py)} Python files.")
