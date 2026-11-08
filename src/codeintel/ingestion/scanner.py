"""Walk a repository, decide what's worth analysing, detect languages."""
from __future__ import annotations

import os
from pathlib import Path

from ..config import Settings
from ..models import FileInfo

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "build", "dist",
             ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "site-packages",
             "vendor", "target", ".next", ".gradle", "coverage", "htmlcov"}
LANG = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
        ".java": "java", ".go": "go", ".rb": "ruby", ".rs": "rust", ".php": "php", ".c": "c", ".h": "c",
        ".cpp": "cpp", ".cs": "csharp", ".kt": "kotlin", ".swift": "swift", ".sql": "sql", ".sh": "shell",
        ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml", ".md": "markdown", ".ini": "ini",
        ".cfg": "ini", ".html": "html", ".css": "css"}
CODE_LANGS = {"python", "javascript", "typescript", "java", "go", "ruby", "rust", "php", "c", "cpp", "csharp",
              "kotlin", "swift", "sql", "shell"}
SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials", ".netrc",
                   ".npmrc", ".pypirc", "secrets.json", "service-account.json"}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def is_sensitive(name: str) -> bool:
    n = name.lower()
    return n in SENSITIVE_NAMES or n.endswith(SENSITIVE_SUFFIXES) or n.startswith(".env.")


def is_test_path(rel: str) -> bool:
    p = rel.lower().replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    return "/tests/" in f"/{p}" or "/test/" in f"/{p}" or base.startswith("test_") or base.endswith(
        ("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts", "_test.go"))


def looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(2048)
    except OSError:
        return True


class ScanResult:
    def __init__(self) -> None:
        self.files: list[FileInfo] = []
        self.sensitive_files: list[str] = []      # reported, never read
        self.skipped: int = 0
        self.truncated: bool = False


def scan_repository(root: Path, settings: Settings) -> ScanResult:
    res = ScanResult()
    root = root.resolve()
    for cur, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(cur) / d).is_symlink()]
        for name in sorted(names):
            p = Path(cur) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                res.skipped += 1
                continue
            if is_sensitive(name):
                res.sensitive_files.append(rel)       # note its existence, never ingest contents
                continue
            lang = LANG.get(p.suffix.lower())
            if lang is None:
                res.skipped += 1
                continue
            size = p.stat().st_size
            if size > settings.max_file_kb * 1024 or looks_binary(p):
                res.skipped += 1
                continue
            if len(res.files) >= settings.max_files:
                res.truncated = True
                return res
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").count("\n") + 1
            except OSError:
                res.skipped += 1
                continue
            res.files.append(FileInfo(path=rel, language=lang, size=size, lines=lines, is_test=is_test_path(rel)))
    return res


def read_text(root: Path, rel: str) -> str:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep):
        raise ValueError("path escapes repository")
    return p.read_text(encoding="utf-8", errors="replace")
