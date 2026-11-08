from codeintel.agents.supervisor import aggregate, plan
from codeintel.models import Finding, Severity
from codeintel.services import analysis_service
from codeintel.services.repository_service import load_context
from codeintel.tools.secrets import scan_secrets
from codeintel.tools.static_python import bug_checks, duplicate_functions, security_checks
from codeintel.workflow.graph import run_analysis


def cats(findings):
    return {f.category for f in findings}


def test_bug_checks():
    code = "def f(x, acc=[]):\n    try:\n        return int(x)\n    except:\n        pass\n    if x == None:\n        pass\n"
    c = cats(bug_checks("a.py", code))
    assert {"bug.mutable_default", "bug.bare_except", "bug.swallowed_exception", "bug.none_comparison"} <= c


def test_unhandled_exception_is_low_confidence_hypothesis():
    f = [x for x in bug_checks("a.py", "def load(p):\n    return open(p).read()\n") if x.category == "bug.unhandled_exception"][0]
    assert f.confidence < 0.5 and not f.confirmed


def test_security_checks():
    code = ('import os, hashlib, yaml\nPASSWORD = "hunter2hunter2"\n'
            'def f(c, u):\n    os.system(c)\n    eval(u)\n    hashlib.md5(b"x")\n    yaml.load(u)\n'
            '    c.execute("select * from t where a=%s" % u)\n    c.execute("select 1 where a=%s", (u,))\n')
    out = security_checks("a.py", code)
    c = cats(out)
    assert {"security.command_injection", "security.code_execution", "security.weak_hash", "security.unsafe_yaml",
            "security.sql_injection", "security.hardcoded_secret"} <= c
    assert sum(f.category == "security.sql_injection" for f in out) == 1      # parameterised query not flagged


def test_secret_scanner_masks_evidence():
    out = scan_secrets("x.txt", "key = AKIAIOSFODNN7EXAMPLE\n")
    assert out and "AKIAIOSFODNN7EXAMPLE" not in out[0].evidence and out[0].confirmed


def test_duplicate_detection():
    body = "    t = 0\n    for i in range(a):\n        t += i * b\n    if t > 3:\n        t -= 1\n    return t\n"
    out = duplicate_functions({"a.py": f"def one(a, b):\n{body}\ndef two(x, y):\n{body.replace('a', 'x').replace('b', 'y')}"})
    assert len(out) == 1


def test_routing():
    assert plan("find security vulnerabilities") == ["understanding", "security"]
    assert "testing" in plan("generate tests") and "bug" not in plan("generate tests")
    assert len(plan("Analyze this repository for bugs, security issues and missing test coverage.")) >= 4
    assert plan("whatever", ["bug"]) == ["bug"]


def test_aggregate_merges_and_notes_conflict():
    a = Finding(agent="security", category="security.sql_injection", severity=Severity.high, title="a", description="",
                file="x.py", line_start=3, tool="ast")
    b = Finding(agent="security", category="security.bandit.B608", severity=Severity.medium, title="b", description="",
                file="x.py", line_start=3, tool="bandit", confirmed=True, source="static_tool")
    out = aggregate([a, b])
    assert len(out) == 1 and out[0].tool == "bandit" and any("Severity differs" in n for n in out[0].notes)


def test_full_analysis_on_sample(indexed):
    ctx = load_context(indexed)
    report = run_analysis(ctx, "Analyze for bugs, security and tests")
    assert set(report.agents_run) >= {"understanding", "bug", "security", "testing"}
    found = {(f.file, f.category.split(".")[1] if f.category.count(".") > 1 else f.category) for f in report.findings}
    assert any(f.category == "security.secret_exposed" and f.file == "app/auth.py" for f in report.findings)
    assert any(f.file == ".env" for f in report.findings)
    assert report.stats["ai_hypotheses"] == 0 and report.stats["tool_confirmed"] > 0
    assert all(1 <= f.line_start for f in report.findings)
    # evidence must be real text from the file
    for f in report.findings:
        if f.evidence and f.file != ".env" and f.tool == "ast":
            assert f.evidence.splitlines()[0].strip() in ctx.text(f.file)
    tests_art = report.results["testing"].artifacts
    assert tests_art and all(compile(v, k, "exec") for k, v in tests_art.items())
    assert found


def test_never_reads_sensitive_contents(indexed):
    ctx = load_context(indexed)
    blob = " ".join(c.text for c in ctx.index.chunks)
    assert "hunter2" not in blob


def test_chat_retrieval_finds_auth(indexed):
    ans = analysis_service.chat(indexed, "How does authentication and login work?")
    files = {c.file for c in ans.citations}
    assert ans.mode == "retrieval_only" and "app/auth.py" in files


def test_fallback_runner_matches_langgraph(indexed):
    from codeintel.workflow import graph as g
    ctx = load_context(indexed)
    a = g._run_fallback(ctx, "find security issues", None)
    assert a.agents_run == ["understanding", "security"]
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return
    b = g._run_langgraph(ctx, "find security issues", None)
    assert b.agents_run == a.agents_run and b.stats["total_findings"] == a.stats["total_findings"]
