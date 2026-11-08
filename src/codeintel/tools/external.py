"""Wrappers around real analysis tools (ruff, bandit, semgrep). Each is optional: if the binary is
missing the wrapper returns ([], warning). Commands are argument lists (never a shell) and run with a
timeout against a read-only copy of the repo. None of these tools execute the analysed code."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import Settings
from ..models import Finding, Severity

_SEV = {"HIGH": Severity.high, "MEDIUM": Severity.medium, "LOW": Severity.low,
        "ERROR": Severity.high, "WARNING": Severity.medium, "INFO": Severity.low}


def _find(binary: str) -> list[str] | None:
    exe = shutil.which(binary)
    if exe:
        return [exe]
    # tools installed in the same venv but not on PATH
    cand = Path(sys.executable).parent / binary
    return [str(cand)] if cand.exists() else None


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    # Pass only the variables the tools need, so API keys and other secrets in our own
    # environment never reach third-party tools. Works on Windows, macOS and Linux.
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG")
    env = {k: v for k, v in os.environ.items() if k in keep}
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def _line(src: Path, rel: str, line: int, span: int = 3) -> str:
    try:
        ls = (src / rel).read_text(errors="replace").splitlines()
        return "\n".join(ls[max(line - 1, 0):line - 1 + span])[:500]
    except OSError:
        return ""


def run_ruff(root: Path, settings: Settings) -> tuple[list[Finding], list[str]]:
    exe = _find("ruff")
    if not exe:
        return [], ["ruff not installed - skipped lint pass"]
    try:
        # --isolated: ignore the repo's own config (it is untrusted). F = pyflakes (real bugs), B = bugbear.
        p = _run([*exe, "check", "--isolated", "--select", "F,B", "--output-format", "json", "--no-cache", "."],
                 root, settings.tool_timeout_s)
        items = json.loads(p.stdout or "[]")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return [], [f"ruff failed: {e}"]
    out = []
    for it in items:
        rel = Path(it["filename"]).resolve().relative_to(root.resolve()).as_posix()
        line = it["location"]["row"]
        sev = Severity.medium if it["code"].startswith(("F8", "F6", "F7", "B0")) else Severity.low
        out.append(Finding(agent="bug", category=f"bug.ruff.{it['code']}", severity=sev, title=f"{it['code']}: {it['message']}",
                           description=f"Ruff rule {it['code']}.", file=rel, line_start=line,
                           line_end=it["end_location"]["row"], evidence=_line(root, rel, line), source="static_tool",
                           confirmed=True, confidence=0.9, tool="ruff"))
    return out, []


def run_bandit(root: Path, settings: Settings) -> tuple[list[Finding], list[str]]:
    exe = _find("bandit")
    if not exe:
        return [], ["bandit not installed - skipped"]
    try:
        p = _run([*exe, "-r", ".", "-f", "json", "-q", "--exclude", "./tests,./test"], root, settings.tool_timeout_s)
        items = json.loads(p.stdout or "{}").get("results", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return [], [f"bandit failed: {e}"]
    out = []
    for it in items:
        rel = Path(it["filename"]).as_posix().removeprefix("./")
        out.append(Finding(
            agent="security", category=f"security.bandit.{it['test_id']}",
            severity=_SEV.get(it["issue_severity"], Severity.low), title=f"{it['test_id']}: {it['issue_text']}",
            description=f"Bandit {it['test_name']} (confidence {it['issue_confidence']}).", file=rel,
            line_start=it["line_number"], line_end=it["line_number"], evidence=re.sub(r"(?m)^\d+ ", "", it["code"]).rstrip()[:500],
            suggestion=f"See bandit docs: {it['more_info']}" if it.get("more_info") else "", source="static_tool", confirmed=True,
            confidence={"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}.get(it["issue_confidence"], 0.5), tool="bandit"))
    return out, []


def run_semgrep(root: Path, settings: Settings) -> tuple[list[Finding], list[str]]:
    exe = _find("semgrep")
    if not exe or not settings.semgrep_config:
        return [], ["semgrep not configured (install semgrep and set CODEINTEL_SEMGREP_CONFIG) - skipped"]
    try:
        p = _run([*exe, "--config", settings.semgrep_config, "--json", "--metrics", "off", "--quiet", "."],
                 root, settings.tool_timeout_s * 2)
        items = json.loads(p.stdout or "{}").get("results", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return [], [f"semgrep failed: {e}"]
    return [Finding(agent="security", category=f"security.semgrep.{it['check_id'].split('.')[-1]}",
                    severity=_SEV.get(it["extra"].get("severity", "WARNING"), Severity.medium),
                    title=it["extra"]["message"][:120], description=it["check_id"], file=it["path"].removeprefix("./"),
                    line_start=it["start"]["line"], line_end=it["end"]["line"], evidence=it["extra"].get("lines", "")[:500],
                    source="static_tool", confirmed=True, confidence=0.8, tool="semgrep") for it in items], []