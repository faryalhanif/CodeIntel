"""Pydantic data contracts shared by every layer (agents, API, storage)."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"

    @property
    def rank(self) -> int:
        return ["info", "low", "medium", "high", "critical"].index(self.value)


# Where a finding came from. This is what lets the report separate facts from hypotheses.
Source = Literal["static_tool", "heuristic", "llm"]


class Finding(BaseModel):
    id: str = ""
    agent: str
    category: str                      # e.g. "security.sql_injection", "bug.bare_except"
    severity: Severity
    title: str
    description: str
    file: str
    line_start: int = 0
    line_end: int = 0
    evidence: str = ""                 # exact code snippet backing the finding
    suggestion: str = ""
    source: Source = "heuristic"
    confidence: float = 0.7            # 0..1
    confirmed: bool = False            # True only for deterministic tool results
    tool: str = ""                     # "ast", "bandit", "ruff", "semgrep", "secrets", "llm"
    notes: list[str] = Field(default_factory=list)


class SymbolInfo(BaseModel):
    kind: Literal["function", "method", "class", "module"]
    name: str
    qualname: str
    file: str
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    decorators: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    param_annotations: dict[str, str] = Field(default_factory=dict)
    is_async: bool = False
    is_public: bool = True


class FileInfo(BaseModel):
    path: str
    language: str
    size: int
    lines: int
    is_test: bool = False


class Chunk(BaseModel):
    id: int
    file: str
    line_start: int
    line_end: int
    kind: str
    name: str
    text: str


class RepoStatus(str, Enum):
    pending = "pending"
    ingesting = "ingesting"
    ready = "ready"
    failed = "failed"


class RepoInfo(BaseModel):
    id: str
    name: str
    source: str
    status: RepoStatus
    error: str = ""
    file_count: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    created_at: str = ""


class AgentResult(BaseModel):
    agent: str
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)   # name -> text (docs, tests)
    data: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    repo_id: str
    task: str
    agents_run: list[str]
    summary: str
    stats: dict
    findings: list[Finding]
    results: dict[str, AgentResult]
    warnings: list[str] = Field(default_factory=list)


# ---- API request models -------------------------------------------------
class CreateRepoRequest(BaseModel):
    git_url: Optional[str] = None
    local_path: Optional[str] = None


class AnalysisRequest(BaseModel):
    task: str = "Analyze this repository for bugs, security issues and missing test coverage."
    agents: Optional[list[str]] = None


class ChatRequest(BaseModel):
    question: str
    top_k: int = 8


class Citation(BaseModel):
    file: str
    line_start: int
    line_end: int
    score: float
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    mode: Literal["llm", "retrieval_only", "index"]
    citations: list[Citation]
