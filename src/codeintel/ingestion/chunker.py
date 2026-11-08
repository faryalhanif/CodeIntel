"""Turn parsed files into retrievable chunks: one per function/class, windows for the rest."""
from __future__ import annotations

from ..models import Chunk
from .parser import ParsedFile

MAX_CHUNK_LINES = 80
WINDOW = 60
OVERLAP = 10


def _window(file: str, lines: list[str], start: int, end: int, kind: str, name: str, cid: int) -> list[Chunk]:
    out: list[Chunk] = []
    s = start
    while s <= end:
        e = min(end, s + WINDOW - 1)
        body = "\n".join(lines[s - 1:e])
        if body.strip():
            out.append(Chunk(id=cid + len(out), file=file, line_start=s, line_end=e, kind=kind, name=name,
                             text=f"# {file}:{s}-{e} ({kind} {name})\n{body}"))
        if e == end:
            break
        s = e - OVERLAP + 1
    return out


def chunk_file(parsed: ParsedFile, text: str, start_id: int) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] = []
    covered = [False] * (len(lines) + 2)
    # leaf-most symbols: methods/functions chunk on their own; classes only contribute their header
    for s in sorted(parsed.symbols, key=lambda s: (s.line_start, -s.line_end)):
        if s.kind == "class":
            end = min(s.line_end, s.line_start + 5)
        else:
            end = s.line_end
        if end - s.line_start + 1 > MAX_CHUNK_LINES:
            chunks += _window(parsed.path, lines, s.line_start, end, s.kind, s.qualname, start_id + len(chunks))
        else:
            body = "\n".join(lines[s.line_start - 1:end])
            doc = f"\n# docstring: {s.docstring[:200]}" if s.docstring else ""
            chunks.append(Chunk(id=start_id + len(chunks), file=parsed.path, line_start=s.line_start, line_end=end,
                                kind=s.kind, name=s.qualname,
                                text=f"# {parsed.path}:{s.line_start}-{end} ({s.kind} {s.qualname}){doc}\n{body}"))
        for i in range(s.line_start, end + 1):
            covered[i] = True
    # remaining top-level code (module docstrings, constants, scripts, configs, markdown...)
    i = 1
    while i <= len(lines):
        if covered[i]:
            i += 1
            continue
        j = i
        while j <= len(lines) and not covered[j]:
            j += 1
        if any(x.strip() for x in lines[i - 1:j - 1]):
            chunks += _window(parsed.path, lines, i, j - 1, "module", parsed.path, start_id + len(chunks))
        i = j
    return chunks
