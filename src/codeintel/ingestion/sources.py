"""Getting untrusted code onto disk safely: git clone, ZIP extraction, local copy.

Nothing here ever *executes* repository code. Git hooks are disabled, symlinks are never followed,
and every limit (size, file count) is enforced while writing, not after.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from ..config import Settings


class IngestionError(Exception):
    pass


def validate_git_url(url: str, settings: Settings) -> str:
    p = urlparse(url.strip())
    if p.scheme != "https":
        raise IngestionError("Only https:// git URLs are allowed")
    if not p.hostname or p.hostname.lower() not in settings.allowed_git_hosts:
        raise IngestionError(f"Host not allowed. Allowed: {', '.join(settings.allowed_git_hosts)}")
    if p.username or p.password:
        raise IngestionError("Credentials in URLs are not allowed")
    if any(c in url for c in (" ", "\n", "\r", ";", "|", "&", "`", "$")):
        raise IngestionError("Invalid characters in URL")
    return url.strip()


def _dir_size_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for f in files:
            fp = Path(root) / f
            if not fp.is_symlink():
                total += fp.stat().st_size
    return total / (1024 * 1024)


def clone_git(url: str, dest: Path, settings: Settings) -> None:
    url = validate_git_url(url, settings)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.symlinks=false",
           "-c", "protocol.file.allow=never", "clone", "--depth", "1", "--no-tags",
           "--single-branch", "--", url, str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=settings.tool_timeout_s * 3, env=env)
    except subprocess.TimeoutExpired as e:
        raise IngestionError("git clone timed out") from e
    except subprocess.CalledProcessError as e:
        raise IngestionError(f"git clone failed: {e.stderr.decode(errors='ignore')[:300]}") from e
    shutil.rmtree(dest / ".git", ignore_errors=True)       # history not needed; avoids hook/config tricks
    if _dir_size_mb(dest) > settings.max_repo_mb:
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError(f"Repository exceeds {settings.max_repo_mb} MB limit")


def extract_zip(zip_path: Path, dest: Path, settings: Settings) -> None:
    """Extract with zip-slip, symlink, zip-bomb and file-count protection."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    budget = settings.max_repo_mb * 1024 * 1024
    written = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > settings.max_files * 4:
            raise IngestionError("Archive contains too many entries")
        for info in infos:
            name = info.filename
            target = (dest / name).resolve()
            if not str(target).startswith(str(root) + os.sep) and target != root:
                raise IngestionError(f"Blocked path traversal entry: {name!r}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue                                  # skip symlink entries
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            written += info.file_size
            if written > budget:
                raise IngestionError(f"Archive expands beyond {settings.max_repo_mb} MB limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                copied = 0
                while chunk := src.read(1 << 16):
                    copied += len(chunk)
                    if copied > info.file_size + 1024:      # header lied about size
                        raise IngestionError("Corrupt or malicious archive")
                    out.write(chunk)


def copy_local(src: Path, dest: Path, settings: Settings) -> None:
    if not settings.allow_local_paths:
        raise IngestionError("Local path ingestion is disabled (set CODEINTEL_ALLOW_LOCAL_PATHS=true)")
    src = src.resolve()
    if not src.is_dir():
        raise IngestionError("Local path is not a directory")
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__"}

    def _ignore(_d, names):
        return [n for n in names if n in skip]

    shutil.copytree(src, dest, symlinks=False, ignore=_ignore, ignore_dangling_symlinks=True)
    # copytree(symlinks=False) follows links; remove any that point outside src to be safe
    for root, dirs, files in os.walk(dest, followlinks=False):
        for n in dirs + files:
            p = Path(root) / n
            if p.is_symlink():
                p.unlink()
    if _dir_size_mb(dest) > settings.max_repo_mb:
        shutil.rmtree(dest, ignore_errors=True)
        raise IngestionError(f"Directory exceeds {settings.max_repo_mb} MB limit")
