from __future__ import annotations

from .. import db
from ..agents.understanding import answer_question
from ..models import ChatResponse
from ..workflow.graph import run_analysis
from .repository_service import load_context


def run_analysis_job(analysis_id: str, repo_id: str, task: str, agents: list[str] | None) -> None:
    try:
        ctx = load_context(repo_id, task)
        report = run_analysis(ctx, task, agents)
        db.finish_analysis(analysis_id, report)
    except Exception as e:
        db.finish_analysis(analysis_id, None, error=f"{type(e).__name__}: {e}"[:500])


def chat(repo_id: str, question: str, top_k: int = 8) -> ChatResponse:
    return answer_question(load_context(repo_id, question), question, top_k=top_k)
