"""REST API. Run: uvicorn codeintel.api.main:app --port 8000   (docs at /docs)"""
from __future__ import annotations

import hmac
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import RedirectResponse

from .. import __version__, db
from ..agents.supervisor import AGENTS, plan
from ..config import get_settings
from ..ingestion.sources import IngestionError, validate_git_url
from ..models import (
    AnalysisRequest,
    ChatRequest,
    ChatResponse,
    CreateRepoRequest,
    RepoInfo,
    RepoStatus,
)
from ..services import analysis_service, repository_service


def require_key(x_api_key: str = Header(default="")) -> None:
    key = get_settings().api_key
    if key and not hmac.compare_digest(key, x_api_key):
        raise HTTPException(401, "Invalid or missing X-API-Key")


app = FastAPI(title="CodeIntel", version=__version__, dependencies=[Depends(require_key)],
              description="Autonomous software engineering & code intelligence platform")
ID = re.compile(r"^[a-f0-9]{12}$")


def _repo_or_404(rid: str) -> RepoInfo:
    if not ID.match(rid):
        raise HTTPException(400, "Invalid repository id")
    repo = db.get_repo(rid)
    if not repo:
        raise HTTPException(404, "Repository not found")
    return repo


def _ready_or_409(rid: str) -> RepoInfo:
    repo = _repo_or_404(rid)
    if repo.status != RepoStatus.ready:
        raise HTTPException(409, f"Repository is {repo.status.value}" + (f": {repo.error}" if repo.error else ""))
    return repo
    

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict:
    from ..llm.client import get_llm
    s = get_settings()
    return {"status": "ok", "version": __version__, "llm_available": get_llm(s).available,
            "llm_model": s.ollama_model, "agents": sorted(AGENTS)}


@app.post("/repositories", response_model=RepoInfo, status_code=202)
def create_repository(req: CreateRepoRequest, bg: BackgroundTasks) -> RepoInfo:
    s = get_settings()
    if bool(req.git_url) == bool(req.local_path):
        raise HTTPException(422, "Provide exactly one of git_url or local_path")
    if req.git_url:
        try:
            url = validate_git_url(req.git_url, s)
        except IngestionError as e:
            raise HTTPException(422, str(e)) from e
        repo = db.create_repo(Path(urlparse(url).path).stem or "repo", url)
        bg.add_task(repository_service.ingest, repo.id, "git", url)
    else:
        if not s.allow_local_paths:
            raise HTTPException(403, "Local path ingestion is disabled on this server")
        repo = db.create_repo(Path(req.local_path).name or "local", f"local:{req.local_path}")
        bg.add_task(repository_service.ingest, repo.id, "local", req.local_path)
    return repo


@app.post("/repositories/upload", response_model=RepoInfo, status_code=202)
async def upload_repository(bg: BackgroundTasks, file: UploadFile = File(...)) -> RepoInfo:
    s = get_settings()
    s.ensure_dirs()
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(422, "Upload a .zip archive")
    limit = s.max_repo_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(dir=s.data_dir, suffix=".zip", delete=False) as tmp:
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                Path(tmp.name).unlink(missing_ok=True)
                raise HTTPException(413, f"Upload exceeds {s.max_repo_mb} MB")
            tmp.write(chunk)
    repo = db.create_repo(Path(file.filename or "upload").stem[:80], "upload")
    bg.add_task(repository_service.ingest, repo.id, "zip", tmp.name)
    return repo


@app.get("/repositories", response_model=list[RepoInfo])
def list_repositories() -> list[RepoInfo]:
    return db.list_repos()


@app.get("/repositories/{rid}", response_model=RepoInfo)
def get_repository(rid: str) -> RepoInfo:
    return _repo_or_404(rid)


@app.delete("/repositories/{rid}", status_code=204)
def delete_repository(rid: str) -> None:
    _repo_or_404(rid)
    repository_service.remove_repository(rid)


@app.post("/repositories/{rid}/analysis", status_code=202)
def start_analysis(rid: str, req: AnalysisRequest, bg: BackgroundTasks) -> dict:
    _ready_or_409(rid)
    try:
        selected = plan(req.task, req.agents)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    aid = db.create_analysis(rid, req.task)
    bg.add_task(analysis_service.run_analysis_job, aid, rid, req.task, req.agents)
    return {"analysis_id": aid, "status": "running", "agents": selected}


@app.get("/analyses/{aid}")
def get_analysis(aid: str) -> dict:
    a = db.get_analysis(aid) if ID.match(aid) else None
    if not a:
        raise HTTPException(404, "Analysis not found")
    return a


@app.get("/repositories/{rid}/overview")
def overview(rid: str) -> dict:
    _ready_or_409(rid)
    from ..agents.understanding import UnderstandingAgent
    from ..llm.client import NullLLM
    ctx = repository_service.load_context(rid)
    ctx.llm = NullLLM()          # overview must be instant; the AI summary is produced by full analyses
    return UnderstandingAgent().safe_run(ctx).model_dump()


@app.post("/repositories/{rid}/chat", response_model=ChatResponse)
def chat(rid: str, req: ChatRequest) -> ChatResponse:
    _ready_or_409(rid)
    if not req.question.strip() or len(req.question) > 2000:
        raise HTTPException(422, "Question must be 1-2000 characters")
    return analysis_service.chat(rid, req.question, min(max(req.top_k, 1), 20))
