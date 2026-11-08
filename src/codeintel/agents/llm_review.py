"""LLM-assisted review used by the Bug and Security agents.

The LLM only ever produces *hypotheses*. Each one must quote the code it is talking about; the quote is
verified against the real file and the finding is discarded if it can't be found (anti-hallucination)."""
from __future__ import annotations

import re

from ..llm.client import UNTRUSTED_NOTICE, parse_json_loose, wrap_code
from ..models import Finding, Severity, SymbolInfo
from .base import AgentContext

SYSTEM = (
    "You are a meticulous senior code reviewer. {focus} Report only concrete problems visible in the code shown. "
    "For each, quote the exact offending line(s) from the code. If you see no real problem return []. "
    'Respond ONLY with JSON: [{{"title": str, "severity": "low|medium|high", "quote": str, '
    '"description": str, "suggestion": str}}]. ' + UNTRUSTED_NOTICE
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def llm_review(ctx: AgentContext, agent: str, symbols: list[SymbolInfo], focus: str, category: str,
               max_symbols: int = 5) -> tuple[list[Finding], list[str]]:
    if not ctx.llm.available:
        return [], ["LLM unavailable - skipped AI review pass (deterministic findings only)"]
    out: list[Finding] = []
    warnings: list[str] = []
    for s in symbols[:max_symbols]:
        lines = ctx.text(s.file).splitlines()
        code = "\n".join(lines[s.line_start - 1:s.line_end])[:6000]
        try:
            raw = ctx.llm.chat(SYSTEM.format(focus=focus), wrap_code(code), json_mode=True)
        except Exception as e:
            warnings.append(f"LLM call failed for {s.qualname}: {e}")
            continue
        data = parse_json_loose(raw)
        if isinstance(data, dict):
            data = data.get("issues") or data.get("findings") or []
        if not isinstance(data, list):
            continue
        for item in data[:5]:
            if not isinstance(item, dict) or not item.get("quote"):
                continue
            quote = str(item["quote"])
            if _norm(quote) not in _norm(code):                     # evidence check
                continue
            offset = next((i for i, ln in enumerate(lines[s.line_start - 1:s.line_end], s.line_start)
                           if _norm(quote.splitlines()[0]) in _norm(ln)), s.line_start)
            sev = {"low": Severity.low, "medium": Severity.medium, "high": Severity.high}.get(
                str(item.get("severity", "low")).lower(), Severity.low)
            out.append(Finding(
                agent=agent, category=f"{category}.llm", severity=sev, title=str(item.get("title", "Possible issue"))[:140],
                description=str(item.get("description", ""))[:800], file=s.file, line_start=offset,
                line_end=offset + quote.count("\n"), evidence=quote[:500], suggestion=str(item.get("suggestion", ""))[:500],
                source="llm", confidence=0.4, confirmed=False, tool="llm",
                notes=["AI-suggested hypothesis: not confirmed by a static tool. Verify before acting."]))
    return out, warnings
