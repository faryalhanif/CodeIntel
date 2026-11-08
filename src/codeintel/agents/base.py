"""Agent contract. Every agent has a role, declared tools, a typed output and hard limits:
agents are READ-ONLY. They receive an AgentContext and return an AgentResult; they cannot write to the
repository, run shell commands chosen by an LLM, or call each other. The supervisor coordinates them."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..config import Settings
from ..ingestion.scanner import read_text
from ..llm.client import LLM
from ..models import AgentResult, Finding
from ..retrieval.embeddings import Embedder
from ..retrieval.store import RepoIndex


@dataclass
class AgentContext:
    repo_id: str
    root: Path
    index: RepoIndex
    embedder: Embedder
    llm: LLM
    settings: Settings
    task: str = ""
    _text_cache: dict[str, str] = field(default_factory=dict)

    def text(self, rel: str) -> str:
        if rel not in self._text_cache:
            self._text_cache[rel] = read_text(self.root, rel)
        return self._text_cache[rel]

    def files(self, language: str | None = None, tests: bool | None = None) -> list[str]:
        return [f.path for f in self.index.files
                if (language is None or f.language == language) and (tests is None or f.is_test == tests)]


class BaseAgent:
    name: ClassVar[str] = "base"
    role: ClassVar[str] = ""
    tools: ClassVar[tuple[str, ...]] = ()
    writes_files: ClassVar[bool] = False          # contract: no agent may modify the repository

    def run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover
        raise NotImplementedError

    def safe_run(self, ctx: AgentContext) -> AgentResult:
        try:
            res = self.run(ctx)
        except Exception as e:  # one failing agent must not sink the whole analysis
            return AgentResult(agent=self.name, summary=f"{self.name} agent failed", warnings=[f"{type(e).__name__}: {e}"])
        for f in res.findings:
            f.agent = f.agent or self.name
            f.id = f.id or hashlib.sha1(f"{f.category}|{f.file}|{f.line_start}|{f.title}".encode()).hexdigest()[:10]
        return res


def finding_key(f: Finding) -> str:
    return f"{f.file}:{f.line_start}:{f.category}"
