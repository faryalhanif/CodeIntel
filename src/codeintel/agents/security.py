from __future__ import annotations

from ..models import AgentResult, Finding, Severity
from ..tools.external import run_bandit, run_semgrep
from ..tools.secrets import scan_secrets
from ..tools.static_python import security_checks
from .base import AgentContext, BaseAgent
from .llm_review import llm_review


class SecurityAgent(BaseAgent):
    name = "security"
    role = "Find security weaknesses: secrets, injection patterns, unsafe APIs, risky dependencies."
    tools = ("secrets_scanner", "ast_security_checks", "bandit", "semgrep", "llm_review")

    def run(self, ctx: AgentContext) -> AgentResult:
        findings: list[Finding] = []
        warnings: list[str] = []
        for f in ctx.index.files:
            text = ctx.text(f.path)
            findings += scan_secrets(f.path, text, skip_generic=f.is_test)
            if f.language == "python" and not f.is_test:
                findings += security_checks(f.path, text)
        for rel in ctx.index.meta.get("sensitive_files", []):
            findings.append(Finding(agent=self.name, category="security.sensitive_file", severity=Severity.high,
                                    title=f"Sensitive file committed: {rel}", file=rel, line_start=1, line_end=1,
                                    description="Credential/key files should not live in the repository. Contents were NOT read.",
                                    suggestion="Remove from history, rotate any secrets, add to .gitignore.",
                                    source="static_tool", confirmed=True, confidence=0.9, tool="scanner"))
        for dep in ctx.index.meta.get("unpinned_dependencies", []):
            findings.append(Finding(agent=self.name, category="security.unpinned_dependency", severity=Severity.low,
                                    title=f"Unpinned dependency: {dep['name']}", file=dep["file"], line_start=dep["line"], line_end=dep["line"],
                                    description="Unpinned dependencies make builds non-reproducible and widen supply-chain exposure.",
                                    evidence=dep["spec"], suggestion="Pin versions (or use a lockfile) and run `pip-audit` for known CVEs.",
                                    source="heuristic", confidence=0.6, tool="deps"))
        for runner in (run_bandit, run_semgrep):
            f, w = runner(ctx.root, ctx.settings)
            findings += f
            warnings += w
        # LLM pass on functions already implicated by deterministic findings
        hot = {(f.file, f.line_start) for f in findings if f.severity.rank >= Severity.medium.rank and f.tool in ("ast", "bandit")}
        cands = [s for s in ctx.index.symbols if s.kind in ("function", "method")
                 and any(s.file == fl and s.line_start <= ln <= s.line_end for fl, ln in hot)]
        llm_f, w = llm_review(ctx, self.name, cands, "Focus on security: injection, authz gaps, unsafe input handling, secrets.", "security")
        findings += llm_f
        warnings += w
        return AgentResult(agent=self.name, findings=findings, warnings=warnings,
                           summary=f"{len(findings)} security findings "
                                   f"({sum(f.confirmed for f in findings)} tool-confirmed).")
