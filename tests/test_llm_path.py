"""LLM behaviour with a stub model: hypotheses must be evidence-checked and never marked confirmed."""
import json

from codeintel.agents.supervisor import build_report
from codeintel.agents.understanding import answer_question
from codeintel.llm.client import parse_json_loose
from codeintel.services.repository_service import load_context
from codeintel.workflow.graph import run_analysis


class StubLLM:
    available = True

    def __init__(self):
        self.prompts = []

    def chat(self, system, user, json_mode=False):
        self.prompts.append((system, user))
        if json_mode:
            return "Sure!\n```json\n" + json.dumps([
                {"title": "Real", "severity": "high", "quote": "return json.load(open(path))",
                 "description": "file handle never closed", "suggestion": "use with"},
                {"title": "Hallucinated", "severity": "high", "quote": "os.remove('/etc/passwd')",
                 "description": "x", "suggestion": "y"}]) + "\n```"
        return "Authentication is handled in app/auth.py [1]."


def test_parse_json_loose():
    assert parse_json_loose('blah ```json\n[{"a": 1}]\n``` bye') == [{"a": 1}]
    assert parse_json_loose("no json here") is None


def test_llm_findings_are_verified_and_never_confirmed(indexed):
    ctx = load_context(indexed)
    ctx.llm = StubLLM()
    report = run_analysis(ctx, "find bugs", ["bug"])
    llm = [f for f in report.findings if f.source == "llm"]
    assert llm and all(not f.confirmed and f.confidence <= 0.5 for f in llm)
    assert all("Hallucinated" != f.title for f in llm)
    assert report.stats["ai_hypotheses"] == len(llm)
    # every LLM prompt wraps repository code and carries the injection notice
    for system, user in ctx.llm.prompts:
        assert "untrusted" in system.lower() and "<repository_code>" in user


def test_chat_uses_llm_with_citations(indexed):
    ctx = load_context(indexed)
    ctx.llm = StubLLM()
    res = answer_question(ctx, "how does authentication work?")
    assert res.mode == "llm" and res.citations


def test_validator_drops_bad_lines(indexed):
    from codeintel.models import AgentResult, Finding, Severity
    ctx = load_context(indexed)
    bad = Finding(agent="bug", category="bug.x", severity=Severity.low, title="t", description="", file="app/auth.py", line_start=9999)
    ghost = Finding(agent="bug", category="bug.x", severity=Severity.low, title="t", description="", file="nope.py", line_start=1)
    rep = build_report(ctx, "t", {"bug": AgentResult(agent="bug", findings=[bad, ghost])})
    assert rep.findings == [] and len(rep.warnings) == 2
