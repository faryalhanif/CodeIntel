"""Central configuration, read from CODEINTEL_* environment variables."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path | None = None) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Real environment variables always win (we never overwrite). Supports comments, blank lines,
    quotes and trailing '# comments'. Looks for ./.env unless CODEINTEL_ENV_FILE points elsewhere.
    """
    env_path = Path(path or os.environ.get("CODEINTEL_ENV_FILE", ".env"))
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:
            value = re.split(r"(?:^|\s)#", value, maxsplit=1)[0].strip()   # drop trailing comment, even if value is empty
        os.environ.setdefault(key, value)


load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = ""):
    """Field factory: reads the environment when Settings() is created, not at import time."""
    return field(default_factory=lambda: os.environ.get(name, default))


def _env_int(name: str, default: int):
    return field(default_factory=lambda: int(os.environ.get(name, str(default))))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CODEINTEL_DATA_DIR", "./data")).resolve())
    api_key: str = _env("CODEINTEL_API_KEY")
    allow_local_paths: bool = field(default_factory=lambda: _bool("CODEINTEL_ALLOW_LOCAL_PATHS", False))
    allowed_git_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip() for h in os.environ.get("CODEINTEL_ALLOWED_GIT_HOSTS", "github.com,gitlab.com,bitbucket.org").split(",") if h.strip()))
    max_repo_mb: int = _env_int("CODEINTEL_MAX_REPO_MB", 200)
    max_files: int = _env_int("CODEINTEL_MAX_FILES", 5000)
    max_file_kb: int = _env_int("CODEINTEL_MAX_FILE_KB", 512)
    embeddings: str = _env("CODEINTEL_EMBEDDINGS", "auto")
    st_model: str = _env("CODEINTEL_ST_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    llm: str = _env("CODEINTEL_LLM", "auto")
    llm_timeout_s: int = _env_int("CODEINTEL_LLM_TIMEOUT", 300)
    ollama_embed_model: str = _env("CODEINTEL_OLLAMA_EMBED_MODEL", "all-minilm:l6-v2")
    ollama_url: str = _env("CODEINTEL_OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = _env("CODEINTEL_OLLAMA_MODEL", "qwen2.5-coder:7b")
    semgrep_config: str = _env("CODEINTEL_SEMGREP_CONFIG")
    tool_timeout_s: int = _env_int("CODEINTEL_TOOL_TIMEOUT", 120)

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "codeintel.db"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.repos_dir, self.index_dir):
            p.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    # Re-read env each call so tests can monkeypatch; cheap enough.
    return Settings()