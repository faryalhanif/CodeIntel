"""Supervisor: routes the task to agents, aggregates, de-duplicates, validates evidence, builds the report."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..models import AgentResult, AnalysisReport, Finding, Severity
from .base import AgentContext, BaseAgent
from .bug import BugAgent
from .docs import DocumentationAgent
from .security import SecurityAgent
from .testing import TestingAgent
from .understanding import UnderstandingAgent

AGENTS: dict[str, type[BaseAgent]] = {
    "understanding": UnderstandingAgent, "bug": BugAgent, "security": SecurityAgent,
    "testing": TestingAgent, "documentation": DocumentationAgent,
}
ROUTES = {
    "bug": r"\b(bug|bugs|defect|exception|error|crash|none|null|duplicate|lint)\w*",
    "security": r"\b(secur|vulnerab|secret|credential|inject|auth|cve|owasp|attack|unsafe)\w*",
    "testing": r"\b(test|tests|testing|coverage|pytest|edge case)\w*",
    "documentation": r"\b(doc|docs|document|documentation|readme|docstring|api reference)\w*",
    "understanding": r"\b(overview|architecture|understand|explain|structure|how does|summary|summari[sz]e)\w*",
}


def plan(task: str, requested: list[str] | None = None) -> list[str]:
    """Deterministic routing. Explicit `requested` wins; otherwise keyword match; otherwise run everything."""
    if requested:
        bad = [a for a in requested if a not in AGENTS]
        if bad:
            raise ValueError(f"Unknown agents: {bad}. Valid: {sorted(AGENTS)}")
        return [a for a in AGENTS if a in requested]
    picked = [a for a, rx in ROUTES.items() if re.search(rx, task, re.I)]
    broad = re.search(r"\b(everything|full|complete|all|analy[sz]e|review|audit)\b", task, re.I)
    if not picked or (broad and len(picked) < 2):
        picked = list(AGENTS)
    if "understanding" not in picked:
        picked.insert(0, "understanding")        # cheap, and gives every report architectural context
    return [a for a in AGENTS if a in picked]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def validate(ctx: AgentContext, findings: list[Finding]) -> tuple[list[Finding], list[str]]:
    """Drop findings whose evidence doesn't check out against the real files."""
    ok, warns, known = [], [], {f.path for f in ctx.index.files} | set(ctx.index.meta.get("sensitive_files", [])) | set(ctx.index.meta.get("dependency_files", []))
    for f in findings:
        if f.file not in known:
            warns.append(f"dropped finding for unknown file {f.file}")
            continue
        if f.file in set(ctx.index.meta.get("sensitive_files", [])):
            ok.append(f)                          # contents intentionally unread
            continue
        text = ctx.text(f.file)
        n = text.count("\n") + 1
        if not (1 <= f.line_start <= n):
            warns.append(f"dropped {f.category} at {f.file}:{f.line_start} (line out of range)")
            continue
        if f.source == "llm" and _norm(f.evidence) not in _norm(text):
            warns.append(f"dropped unverifiable AI finding at {f.file}:{f.line_start}")
            continue
        ok.append(f)
    return ok, warns


def aggregate(findings: list[Finding]) -> list[Finding]:
    """Merge findings pointing at the same agent+file+line; keep the strongest, note corroboration/conflicts."""
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[(f.agent, f.file, f.line_start)].append(f)
    merged: list[Finding] = []
    for grp in groups.values():
        grp.sort(key=lambda f: (f.confirmed, f.severity.rank, f.confidence), reverse=True)
        primary = grp[0]
        seen = {(primary.tool, primary.category)}
        for other in grp[1:]:
            if (other.tool, other.category) in seen:
                continue
            seen.add((other.tool, other.category))
            primary.notes.append(f"Also flagged by {other.tool}: {other.title}")
            if other.severity != primary.severity:
                primary.notes.append(f"Severity differs between tools ({primary.tool}={primary.severity.value}, "
                                     f"{other.tool}={other.severity.value}); kept the higher one.")
                if other.severity.rank > primary.severity.rank:
                    primary.severity = other.severity
            if other.tool != primary.tool:
                primary.confidence = round(min(0.99, primary.confidence + 0.1), 2)     # corroboration
        merged.append(primary)
    merged.sort(key=lambda f: (-f.severity.rank, -f.confirmed, -f.confidence, f.file, f.line_start))
    return merged


def build_report(ctx: AgentContext, task: str, results: dict[str, AgentResult]) -> AnalysisReport:
    raw = [f for r in results.values() for f in r.findings]
    valid, warns = validate(ctx, raw)
    final = aggregate(valid)
    by_sev = Counter(f.severity.value for f in final)
    by_agent = Counter(f.agent for f in final)
    confirmed = sum(f.confirmed for f in final)
    llm_n = sum(f.source == "llm" for f in final)
    stats = {"total_findings": len(final), "by_severity": dict(by_sev), "by_agent": dict(by_agent),
             "tool_confirmed": confirmed, "heuristic": sum(f.source == "heuristic" for f in final),
             "ai_hypotheses": llm_n, "llm_used": ctx.llm.available}
    top = [f for f in final if f.severity.rank >= Severity.high.rank][:3]
    lines = [f"Ran {', '.join(results)}. {len(final)} findings: "
             + ", ".join(f"{by_sev.get(s, 0)} {s}" for s in ("critical", "high", "medium", "low", "info") if by_sev.get(s))
             + f". {confirmed} confirmed by tools, {llm_n} AI-suggested hypotheses."]
    if top:
        lines.append("Most urgent: " + "; ".join(f"{f.title} ({f.file}:{f.line_start})" for f in top) + ".")
    for r in results.values():
        if r.summary:
            lines.append(f"[{r.agent}] {r.summary}")
    warnings = list(dict.fromkeys([w for r in results.values() for w in r.warnings] + warns))
    return AnalysisReport(repo_id=ctx.repo_id, task=task, agents_run=list(results), summary="\n".join(lines),
                          stats=stats, findings=final, results=results, warnings=warnings)