"""Per-repository index: chunks + vectors + dependency graph, persisted under data/index/<repo_id>/."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np

from ..ingestion.graph import graph_from_dict, graph_to_dict
from ..models import Chunk, FileInfo, SymbolInfo
from .embeddings import Embedder, tokenize


@dataclass
class RepoIndex:
    chunks: list[Chunk]
    vectors: np.ndarray
    graph: nx.DiGraph
    files: list[FileInfo]
    symbols: list[SymbolInfo]
    embedder_name: str
    meta: dict

    # ---- persistence -------------------------------------------------
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        payload = {
            "chunks": [c.model_dump() for c in self.chunks],
            "files": [f.model_dump() for f in self.files],
            "symbols": [s.model_dump() for s in self.symbols],
            "graph": graph_to_dict(self.graph),
            "embedder": self.embedder_name,
            "meta": self.meta,
        }
        (directory / "index.json").write_text(json.dumps(payload))

    @classmethod
    def load(cls, directory: Path) -> "RepoIndex":
        d = json.loads((directory / "index.json").read_text())
        return cls(
            chunks=[Chunk(**c) for c in d["chunks"]], vectors=np.load(directory / "vectors.npy"),
            graph=graph_from_dict(d["graph"]), files=[FileInfo(**f) for f in d["files"]],
            symbols=[SymbolInfo(**s) for s in d["symbols"]], embedder_name=d["embedder"], meta=d["meta"])

    # ---- search ------------------------------------------------------
    def search(self, query: str, embedder: Embedder, k: int = 8, expand_graph: bool = True) -> list[tuple[Chunk, float]]:
        """Hybrid retrieval: cosine similarity + keyword overlap, then pull in graph neighbours."""
        if not self.chunks:
            return []
        qv = embedder.encode([query])[0]
        sims = self.vectors @ qv
        qtok = set(tokenize(query))
        if qtok:
            kw = np.array([len(qtok & set(tokenize(c.name + " " + c.text[:1500]))) / len(qtok) for c in self.chunks])
        else:
            kw = np.zeros(len(self.chunks))
        score = 0.65 * sims + 0.35 * kw
        order = np.argsort(-score)[:k]
        results = [(self.chunks[i], float(score[i])) for i in order if score[i] > 0]
        if expand_graph and results:
            seen = {c.id for c, _ in results}
            top_files = [c.file for c, _ in results[:3]]
            extra: list[tuple[Chunk, float]] = []
            for f in dict.fromkeys(top_files):
                if f not in self.graph:
                    continue
                for nb in list(self.graph.successors(f)) + list(self.graph.predecessors(f)):
                    best = max((i for i, c in enumerate(self.chunks) if c.file == nb), key=lambda i: score[i], default=None)
                    if best is not None and self.chunks[best].id not in seen and score[best] > 0.05:
                        seen.add(self.chunks[best].id)
                        extra.append((self.chunks[best], float(score[best]) * 0.8))
            results += sorted(extra, key=lambda t: -t[1])[:3]
        return results
