"""Dependency graph between repository files (NetworkX)."""
from __future__ import annotations

import networkx as nx
from networkx.readwrite import json_graph

from .parser import ParsedFile


def _py_module_index(paths: list[str]) -> dict[str, str]:
    idx: dict[str, str] = {}
    for p in paths:
        if not p.endswith(".py"):
            continue
        mod = p[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        idx[mod] = p
        # also index by suffix so `import auth` resolves when package root is nested ("src/app/auth" -> "app.auth")
        parts = mod.split(".")
        for i in range(1, len(parts)):
            idx.setdefault(".".join(parts[i:]), p)
    return idx


def _resolve_py(mod: str, level: int, names: list[str], importer: str, idx: dict[str, str]) -> set[str]:
    out: set[str] = set()
    base = mod
    if level:
        pkg = importer.rsplit("/", 1)[0].replace("/", ".") if "/" in importer else ""
        parts = pkg.split(".") if pkg else []
        parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
        base = ".".join([*parts, mod]) if mod else ".".join(parts)
    for cand in [base, *[f"{base}.{n}" if base else n for n in names]]:
        if cand in idx:
            out.add(idx[cand])
    return out


def build_graph(parsed: list[ParsedFile]) -> nx.DiGraph:
    g = nx.DiGraph()
    paths = [p.path for p in parsed]
    idx = _py_module_index(paths)
    for p in parsed:
        g.add_node(p.path, language=p.language, symbols=len(p.symbols))
    pathset = set(paths)
    for p in parsed:
        for mod, level, names in p.imports:
            targets: set[str] = set()
            if p.language == "python":
                targets = _resolve_py(mod, level, names, p.path, idx)
            elif mod.startswith("."):                                  # relative JS/TS import
                import posixpath
                base = posixpath.normpath(posixpath.join(posixpath.dirname(p.path), mod))
                for ext in ("", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"):
                    if base + ext in pathset:
                        targets.add(base + ext)
            for t in targets:
                if t != p.path:
                    g.add_edge(p.path, t, kind="imports")
    return g


def graph_to_dict(g: nx.DiGraph) -> dict:
    return json_graph.node_link_data(g, edges="edges")


def graph_from_dict(d: dict) -> nx.DiGraph:
    return json_graph.node_link_graph(d, edges="edges", directed=True)


def central_files(g: nx.DiGraph, n: int = 8) -> list[tuple[str, float]]:
    """Files that the most other files depend on (directly or transitively).

    Edges point importer -> imported, so a file's ancestors are everything that depends on it.
    Pure NetworkX graph traversal: no scipy needed (nx.pagerank requires it).
    Returns [] when no file imports another one: there is no meaningful ranking then."""
    if g.number_of_edges() == 0:
        return []
    scored = [(f, float(len(nx.ancestors(g, f))), g.in_degree(f)) for f in g.nodes if g.in_degree(f) > 0]
    scored.sort(key=lambda t: (-t[1], -t[2], t[0]))
    return [(f, score) for f, score, _ in scored[:n]]