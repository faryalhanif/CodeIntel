"""Regex secret scanner for any text file. Evidence is always masked."""
from __future__ import annotations

import re

from ..models import Finding, Severity

PATTERNS = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), Severity.critical),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), Severity.critical),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), Severity.critical),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), Severity.high),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), Severity.high),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b"), Severity.critical),
    ("Generic credential assignment",
     re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*['"]([^'"\s]{8,})['"]"""),
     Severity.medium),
]


def mask(s: str) -> str:
    return s[:4] + "*" * max(len(s) - 6, 4) + s[-2:] if len(s) > 8 else "*" * len(s)


def scan_secrets(path: str, text: str, skip_generic: bool = False) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        for name, rx, sev in PATTERNS:
            if skip_generic and name.startswith("Generic"):
                continue
            m = rx.search(line)
            if m:
                secret = m.group(1) if m.groups() else m.group(0)
                if re.match(r"(?i)^(changeme|your[_-]|example|xxx|<|\$\{|test)", secret):
                    continue
                out.append(Finding(
                    agent="security", category="security.secret_exposed", severity=sev, title=f"{name} in source",
                    description="Credential-like value committed to the repository. Treat it as compromised.",
                    file=path, line_start=i, line_end=i, evidence=line.replace(secret, mask(secret))[:300].strip(),
                    suggestion="Revoke/rotate the credential and load it from the environment or a secrets manager.",
                    source="static_tool", confirmed=True, confidence=0.9, tool="secrets"))
    return out
