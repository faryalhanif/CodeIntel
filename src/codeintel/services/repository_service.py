"""Ingestion pipeline: fetch -> scan -> parse -> graph -> chunk -> embed -> persist."""
from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import tomllib

from .. import db
from ..agents.base import AgentContext
from ..config import Settings, get_settings
from ..ingestion.chunker import chunk_file
from ..ingestion.graph import build_graph
from ..ingestion.parser import ParsedFile, parse_file
from ..ingestion.scanner import read_text, scan_repository
from ..ingestion.sources import IngestionError, clone_git, copy_local, extract_zip
from ..llm.client import get_llm
from ..models import Chunk, RepoStatus
from ..retrieval.embeddings import get_embedder
from ..retrieval.store import RepoIndex

REPO_ID_RE = re.compile(r"^[a-f0-9]{12}$")
FRAMEWORKS = {"fastapi": "FastAPI", "flask": "Flask", "django": "Django", "starlette": "Starlette", "sqlalchemy": "SQLAlchemy",
              "pydantic": "Pydantic", "pytest": "pytest", "celery": "Celery", "requests": "requests", "numpy": "NumPy",
              "pandas": "pandas", "torch": "PyTorch", "tensorflow": "TensorFlow", "express": "Express", "react": "React",
              "langchain": "LangChain", "streamlit": "Streamlit"}


def repo_dir(rid: str, settings: Settings | None = None) -> Path:
    if not REPO_ID_RE.match(rid):
        raise ValueError("invalid repository id")
    return (settings or get_settings()).repos_dir / rid


def _flatten_single_root(path: Path) -> Path:
    entries = [p for p in path.iterdir()]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else path


def _parse_dependencies(root: Path, files: list[str]) -> tuple[list[str], list[dict]]:
    deps: list[str] = []
    unpinned: list[dict] = []
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if name.startswith("requirements") and name.endswith(".txt"):
            for i, line in enumerate(read_text(root, rel).splitlines(), 1):
                line = line.split("#")[0].strip()
                if not line or line.startswith(("-", "git+", "http")):
                    continue
                m = re.match(r"([A-Za-z0-9_.\-]+)", line)
                if not m:
                    continue
                deps.append(m.group(1).lower())
                if "==" not in line:
                    unpinned.append({"name": m.group(1), "file": rel, "line": i, "spec": line})
        elif name == "pyproject.toml":
            try:
                data = tomllib.loads(read_text(root, rel))
                for d in data.get("project", {}).get("dependencies", []):
                    m = re.match(r"([A-Za-z0-9_.\-]+)", d)
                    if m:
                        deps.append(m.group(1).lower())
            except Exception:
                pass
    return sorted(set(deps)), unpinned


def ingest(repo_id: str, kind: str, source: str, settings: Settings | None = None) -> None:
    """kind: git | local | zip. Runs in a background thread; records status in the DB."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    dest = repo_dir(repo_id, settings)
    db.update_repo(repo_id, status=RepoStatus.ingesting)
    try:
        if kind == "git":
            clone_git(source, dest, settings)
        elif kind == "local":
            copy_local(Path(source), dest, settings)
        elif kind == "zip":
            extract_zip(Path(source), dest, settings)
            Path(source).unlink(missing_ok=True)
        else:
            raise IngestionError(f"unknown source kind {kind}")
        root = _flatten_single_root(dest)
        scan = scan_repository(root, settings)
        if not scan.files:
            raise IngestionError("No analysable source files found")
        parsed: list[ParsedFile] = []
        chunks: list[Chunk] = []
        imports_seen: Counter[str] = Counter()
        entry_points, syntax_errors = [], []
        for f in scan.files:
            text = read_text(root, f.path)
            p = parse_file(f.path, f.language, text)
            parsed.append(p)
            if p.syntax_error:
                syntax_errors.append(f"{f.path}: {p.syntax_error}")
            for mod, _lvl, _names in p.imports:
                imports_seen[mod.split(".")[0].split("/")[0]] += 1
            if f.language == "python" and "__main__" in text and re.search(r"if\s+__name__\s*==", text):
                entry_points.append(f.path)
            chunks += chunk_file(p, text, len(chunks))
        graph = build_graph(parsed)
        embedder = get_embedder(settings)
        vectors = embedder.encode([c.text[:2000] for c in chunks]) if chunks else np.zeros((0, embedder.dim), dtype=np.float32)
        dep_files = [p.relative_to(root).as_posix() for p in root.glob("requirements*.txt")]
        deps, unpinned = _parse_dependencies(root, [f.path for f in scan.files] + dep_files)
        frameworks = sorted({FRAMEWORKS[m.lower()] for m in imports_seen if m.lower() in FRAMEWORKS} |
                            {FRAMEWORKS[d] for d in deps if d in FRAMEWORKS})
        meta = {"root": root.relative_to(dest).as_posix() if root != dest else "", "entry_points": entry_points,
                "frameworks": frameworks, "dependencies": deps, "unpinned_dependencies": unpinned,
                "sensitive_files": scan.sensitive_files, "dependency_files": dep_files, "truncated": scan.truncated, "syntax_errors": syntax_errors}
        index = RepoIndex(chunks=chunks, vectors=vectors, graph=graph, files=scan.files,
                          symbols=[s for p in parsed for s in p.symbols], embedder_name=embedder.name, meta=meta)
        index.save(settings.index_dir / repo_id)
        langs = Counter(f.language for f in scan.files)
        db.update_repo(repo_id, status=RepoStatus.ready, file_count=len(scan.files), languages=dict(langs))
    except Exception as e:
        shutil.rmtree(dest, ignore_errors=True)
        db.update_repo(repo_id, status=RepoStatus.failed, error=str(e)[:500])


def load_context(repo_id: str, task: str = "", settings: Settings | None = None) -> AgentContext:
    settings = settings or get_settings()
    idx_dir = settings.index_dir / repo_id
    if not REPO_ID_RE.match(repo_id) or not (idx_dir / "index.json").exists():
        raise LookupError("repository not indexed")
    index = RepoIndex.load(idx_dir)
    root = repo_dir(repo_id, settings) / index.meta.get("root", "")
    return AgentContext(repo_id=repo_id, root=root, index=index, embedder=get_embedder(settings, index.embedder_name),
                        llm=get_llm(settings), settings=settings, task=task)


def remove_repository(repo_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    shutil.rmtree(repo_dir(repo_id, settings), ignore_errors=True)
    shutil.rmtree(settings.index_dir / repo_id, ignore_errors=True)
    db.delete_repo(repo_id)