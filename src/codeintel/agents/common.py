from __future__ import annotations

import re

from ..models import SymbolInfo

_ROUTE = re.compile(r"""^(?P<obj>\w+)\.(?P<verb>get|post|put|delete|patch|route|websocket)\(\s*(?P<args>.*)\)$""")


def find_endpoints(symbols: list[SymbolInfo]) -> list[dict]:
    out = []
    for s in symbols:
        for d in s.decorators:
            m = _ROUTE.match(d.strip())
            if m:
                path = re.search(r"""['"]([^'"]*)['"]""", m.group("args"))
                verb = m.group("verb").upper()
                if verb == "ROUTE":
                    methods = re.search(r"methods\s*=\s*\[([^\]]*)\]", m.group("args"))
                    verb = re.sub(r"['\"\s]", "", methods.group(1)) if methods else "GET"
                out.append({"method": verb, "path": path.group(1) if path else "?", "handler": s.qualname,
                            "file": s.file, "line": s.line_start, "doc": s.docstring.split("\n")[0] if s.docstring else ""})
    return out


def snippet(text: str, a: int, b: int, limit: int = 600) -> str:
    return "\n".join(text.splitlines()[max(a - 1, 0):b])[:limit]
