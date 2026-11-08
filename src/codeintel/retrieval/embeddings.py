"""Embedding backends. `auto` uses sentence-transformers when installed, else a hashing embedder
(no model download, deterministic, surprisingly decent for identifier-heavy code)."""
from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np
import requests

from ..config import Settings

_TOKEN = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")


def tokenize(text: str) -> list[str]:
    """Split snake_case / camelCase identifiers into lowercase sub-words."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for part in word.split("_"):
            out += [t.lower() for t in _TOKEN.findall(part)]
    # crude stemming: 4-char prefix makes "authentication"/"auth", "tokens"/"token" match
    return [t[:4] if len(t) > 4 else t for t in out if len(t) > 1]


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    name = "hashing-384"
    dim = 384

    def encode(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = tokenize(t)
            feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]   # unigrams + bigrams
            for f in feats:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                mat[i, h % self.dim] += 1.0
            mat[i] = np.log1p(mat[i])
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.maximum(norms, 1e-9)


class STEmbedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency
        self._m = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._m.encode(texts, normalize_embeddings=True, batch_size=32,
                                         show_progress_bar=False), dtype=np.float32)


class OllamaEmbedError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Ollama embeddings failed ({status}): {body}")
        self.status = status


class OllamaEmbedder:
    """Embeddings from a local Ollama model (e.g. all-minilm:l6-v2): no PyTorch install needed.

    Small embedding models only accept ~256 tokens and Ollama can answer HTTP 400 when an input is longer,
    so text is cut client-side. If a batch is still rejected it is retried with shorter text."""

    LADDER = (700, 350, 150)          # max characters per text, tried in order
    BATCH = 16

    def __init__(self, url: str, model: str, timeout: int = 120) -> None:
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.name = f"ollama:{model}"
        self._legacy = False
        self.dim = int(self._embed(["dimension probe"]).shape[1])      # also verifies Ollama + model are reachable

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._legacy:                                                # Ollama < 0.3 only has /api/embeddings
            rows = []
            for t in texts:
                r = requests.post(f"{self.url}/api/embeddings", json={"model": self.model, "prompt": t}, timeout=self.timeout)
                if r.status_code != 200:
                    raise OllamaEmbedError(r.status_code, r.text[:200])
                rows.append(r.json()["embedding"])
            return np.asarray(rows, dtype=np.float32)
        r = requests.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts, "truncate": True},
                          timeout=self.timeout)
        if r.status_code == 404 and "model" not in r.text.lower():      # endpoint missing, not "model not found"
            self._legacy = True
            return self._embed(texts)
        if r.status_code != 200:
            raise OllamaEmbedError(r.status_code, r.text[:200])
        return np.asarray(r.json()["embeddings"], dtype=np.float32)

    def _encode_batch(self, batch: list[str]) -> np.ndarray:
        last: OllamaEmbedError | None = None
        for limit in self.LADDER:
            try:
                return self._embed([t[:limit] or " " for t in batch])
            except OllamaEmbedError as e:
                if e.status != 400:
                    raise
                last = e
        assert last is not None
        raise last

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        mat = np.vstack([self._encode_batch(texts[i:i + self.BATCH]) for i in range(0, len(texts), self.BATCH)])
        return mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)


def get_embedder(settings: Settings, name: str | None = None) -> Embedder:
    """`name` = the embedder an existing index was built with. Queries MUST use the same one, otherwise
    similarity scores are silently meaningless (hashing and MiniLM are both 384-d, so nothing would crash)."""
    if name:
        if name == HashingEmbedder.name:
            return HashingEmbedder()
        if name.startswith("ollama:"):
            return OllamaEmbedder(settings.ollama_url, name.split(":", 1)[1])
        return STEmbedder(name)
    if settings.embeddings == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.ollama_embed_model)
    if settings.embeddings in ("auto", "st"):
        try:
            return STEmbedder(settings.st_model)
        except Exception:
            if settings.embeddings == "st":
                raise
    return HashingEmbedder()