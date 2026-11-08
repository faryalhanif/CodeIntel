import zipfile

import pytest

from codeintel.config import get_settings
from codeintel.ingestion.graph import build_graph
from codeintel.ingestion.parser import parse_file
from codeintel.ingestion.scanner import scan_repository
from codeintel.ingestion.sources import IngestionError, extract_zip, validate_git_url


def test_parser_extracts_symbols_and_imports():
    code = 'import os\nfrom .b import x\n\nclass A:\n    def m(self, p: int): return p\n\nasync def f(a, b=1):\n    """doc"""\n'
    p = parse_file("pkg/a.py", "python", code)
    names = {s.qualname: s.kind for s in p.symbols}
    assert names == {"A": "class", "A.m": "method", "f": "function"}
    assert next(s for s in p.symbols if s.name == "f").is_async
    assert ("os", 0, []) in p.imports


def test_parser_reports_syntax_error():
    assert "line" in parse_file("x.py", "python", "def (:").syntax_error


def test_scanner_skips_sensitive_and_vendor(sample_repo):
    (sample_repo / "node_modules").mkdir()
    (sample_repo / "node_modules" / "x.js").write_text("1")
    res = scan_repository(sample_repo, get_settings())
    paths = {f.path for f in res.files}
    assert ".env" in res.sensitive_files and ".env" not in paths
    assert not any("node_modules" in p for p in paths)
    assert "app/auth.py" in paths


def test_dependency_graph_resolves_imports(sample_repo):
    files = scan_repository(sample_repo, get_settings()).files
    parsed = [parse_file(f.path, f.language, (sample_repo / f.path).read_text()) for f in files]
    g = build_graph(parsed)
    assert g.has_edge("app/auth.py", "app/database.py")
    assert g.has_edge("app/main.py", "app/auth.py")


def test_zip_slip_blocked(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(IngestionError, match="traversal"):
        extract_zip(z, tmp_path / "out", get_settings())
    assert not (tmp_path / "escape.txt").exists()


def test_zip_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEINTEL_MAX_REPO_MB", "1")
    z = tmp_path / "big.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", "0" * (3 * 1024 * 1024))
    with pytest.raises(IngestionError, match="limit"):
        extract_zip(z, tmp_path / "out", get_settings())


@pytest.mark.parametrize("url", ["http://github.com/a/b", "https://evil.com/a/b", "https://u:p@github.com/a/b",
                                 "file:///etc/passwd", "https://github.com/a/b;rm -rf /"])
def test_git_url_validation_rejects(url):
    with pytest.raises(IngestionError):
        validate_git_url(url, get_settings())


def test_git_url_validation_accepts():
    assert validate_git_url("https://github.com/psf/requests", get_settings())
