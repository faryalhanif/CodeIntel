from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from ..ingestion.graph import central_files
from ..llm.client import UNTRUSTED_NOTICE, wrap_code
from ..models import AgentResult, ChatResponse, Citation
from .base import AgentContext, BaseAgent
from .common import find_endpoints


class UnderstandingAgent(BaseAgent):
    name = "understanding"
    role = "Explain the repository: languages, layout, entry points, modules, dependencies, endpoints."
    tools = ("index", "dependency_graph")

    def run(self, ctx: AgentContext) -> AgentResult:
        idx = ctx.index
        langs = Counter()
        for f in idx.files:
            langs[f.language] += f.lines
        code_files = [f for f in idx.files if f.language in {"python", "javascript", "typescript", "java", "go"}]
        by_dir: dict[str, int] = defaultdict(int)
        for f in idx.files:
            by_dir[f.path.split("/")[0] if "/" in f.path else "(root)"] += 1
        central = central_files(idx.graph)
        endpoints = find_endpoints(idx.symbols)
        classes = [s for s in idx.symbols if s.kind == "class"]
        funcs = [s for s in idx.symbols if s.kind in ("function", "method")]
        undocumented = [s for s in funcs if s.is_public and not s.docstring]
        meta = idx.meta
        data = {
            "languages": dict(langs.most_common()), "layout": dict(by_dir), "entry_points": meta.get("entry_points", []),
            "frameworks": meta.get("frameworks", []), "dependencies": meta.get("dependencies", []),
            "central_files": [{"file": f, "score": round(s, 4)} for f, s in central],
            "endpoints": endpoints, "counts": {"files": len(idx.files), "code_files": len(code_files),
                                               "classes": len(classes), "functions": len(funcs),
                                               "undocumented_public": len(undocumented), "tests": sum(f.is_test for f in idx.files)},
            "import_edges": idx.graph.number_of_edges(), "sensitive_files": meta.get("sensitive_files", []),
        }
        md = ["# Architecture overview", "", f"**{len(idx.files)} files**, {len(classes)} classes, {len(funcs)} functions.", "",
              "## Languages (lines)", *[f"- {k}: {v}" for k, v in langs.most_common()], "",
              "## Layout", *[f"- `{k}/` - {v} files" for k, v in sorted(by_dir.items())], ""]
        if data["frameworks"]:
            md += ["## Frameworks detected", ", ".join(data["frameworks"]), ""]
        if data["entry_points"]:
            md += ["## Entry points", *[f"- `{e}`" for e in data["entry_points"]], ""]
        if central:
            md += ["## Most depended-upon files", *[f"- `{f}`" for f, _ in central[:6]], ""]
        edges = list(idx.graph.edges)[:40]
        if edges:
            nid = {n: f"n{i}" for i, n in enumerate(sorted({x for e in edges for x in e}))}
            md += ["## Dependency graph", "```mermaid", "graph LR",
                   *[f'  {nid[n]}["{n}"]' for n in nid], *[f"  {nid[a]} --> {nid[b]}" for a, b in edges], "```", ""]
        summary = (f"{len(idx.files)} files across {len(langs)} languages; {len(endpoints)} HTTP endpoints; "
                   f"{len(undocumented)} public functions lack docstrings.")
        warnings = []
        if ctx.llm.available:
            try:
                facts = "\n".join(md[:40])
                facts += "\nEndpoints:\n" + "\n".join(f"{e['method']} {e['path']} -> {e['handler']} ({e['file']})" for e in endpoints[:25])
                facts += "\nClasses: " + ", ".join(c.qualname for c in classes[:25])
                facts += "\nFunctions: " + ", ".join(s.qualname for s in funcs[:40])
                summary = ctx.llm.chat("You are a software architect. In 5 sentences explain what this project does and how it is "
                                       "organised, naming concrete files, endpoints and classes. Use only the facts given; "
                                       "never invent anything.", facts)
            except Exception as e:
                warnings.append(f"LLM summary failed: {e}")
        return AgentResult(agent=self.name, summary=summary, artifacts={"architecture.md": "\n".join(md)},
                           data=data, warnings=warnings)


log = logging.getLogger("codeintel")

_NOT_STRUCTURAL = re.compile(r"\b(how(?!\s+many)|why|explain|work|works|does|do|happens?|purpose|meaning)\b", re.I)
_LIST_INTENT = re.compile(r"\b(which|what|list|show|where|all|find|how\s+many|number\s+of|count)\b", re.I)
_KINDS = [
    ("endpoint", re.compile(r"\b(endpoints?|routes?)\b", re.I)),
    ("class", re.compile(r"\bclass(es)?\b", re.I)),
    ("function", re.compile(r"\b(functions?|methods?)\b", re.I)),
]


def _structural_answer(ctx: AgentContext, question: str) -> ChatResponse | None:
    """Questions like 'which files contain the classes?' are answered exactly from the code index,
    not by fuzzy search (and not by an LLM that could get it wrong)."""
    if _NOT_STRUCTURAL.search(question) or not _LIST_INTENT.search(question):
        return None
    kind = next((k for k, rx in _KINDS if rx.search(question)), None)
    if kind is None:
        return None
    if kind == "endpoint":
        items = [(e["file"], e["line"], f"{e['method']} {e['path']} -> {e['handler']}") for e in find_endpoints(ctx.index.symbols)]
        label = "HTTP endpoints"
    elif kind == "class":
        items = [(s.file, s.line_start, s.qualname) for s in ctx.index.symbols if s.kind == "class"]
        label = "classes"
    else:
        items = [(s.file, s.line_start, s.qualname) for s in ctx.index.symbols if s.kind in ("function", "method")]
        label = "functions/methods"
    if not items:
        langs = sorted({f.language for f in ctx.index.files})
        return ChatResponse(
            answer=(f"The code index contains no {label}. Symbols are extracted from Python (fully) and from "
                    f"JavaScript/TypeScript/Java/Go (basic). Languages in this repo: {', '.join(langs)}. "
                    "HTML, CSS and config files have no classes/functions to index."),
            mode="index", citations=[])
    by_file: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for f, line, name in items:
        by_file[f].append((line, name))
    lines = [f"Found {len(items)} {label} in {len(by_file)} files (from the code index, not AI-generated):", ""]
    for f in sorted(by_file):
        lines.append(f"- `{f}`: " + ", ".join(f"`{n}` (L{ln})" for ln, n in sorted(by_file[f])[:12])
                     + (" ..." if len(by_file[f]) > 12 else ""))
    cites = [Citation(file=f, line_start=ln, line_end=ln, score=1.0, snippet=n) for f, ln, n in items[:20]]
    return ChatResponse(answer="\n".join(lines), mode="index", citations=cites)


def answer_question(ctx: AgentContext, question: str, top_k: int = 8) -> ChatResponse:
    structural = _structural_answer(ctx, question)
    if structural:
        return structural
    hits = ctx.index.search(question, ctx.embedder, k=top_k)
    cites = [Citation(file=c.file, line_start=c.line_start, line_end=c.line_end, score=round(s, 3),
                      snippet=c.text.split("\n", 1)[-1][:500]) for c, s in hits]
    if not hits:
        return ChatResponse(answer="I couldn't find anything relevant in the indexed code.", mode="retrieval_only", citations=[])
    notice = "No LLM is connected, so here are the most relevant locations (ranked):"
    if ctx.llm.available:
        context = "\n\n".join(f"[{i + 1}] {c.text[:1200]}" for i, (c, _) in enumerate(hits[:5]))
        system = ("You answer questions about a codebase using ONLY the numbered snippets provided. "
                  "Cite evidence as [n] with file:line. If the snippets don't contain the answer, say so. " + UNTRUSTED_NOTICE)
        try:
            ans = ctx.llm.chat(system, f"Question: {question}\n\n{wrap_code(context)}")
            return ChatResponse(answer=ans, mode="llm", citations=cites)
        except Exception as e:
            log.warning("LLM chat failed: %s: %s", type(e).__name__, e)
            notice = (f"The AI model is connected but did not answer ({type(e).__name__}: {str(e)[:150]}). "
                      "It may be too slow for this machine or out of memory. Most relevant locations instead:")
    lines = [notice, ""]
    for c, s in hits[:6]:
        lines.append(f"- `{c.file}:{c.line_start}-{c.line_end}` - {c.kind} `{c.name}` (score {s:.2f})")
    return ChatResponse(answer="\n".join(lines), mode="retrieval_only", citations=cites)