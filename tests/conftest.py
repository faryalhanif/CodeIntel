import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent / "fixtures" / "vulnerable_app"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODEINTEL_ALLOW_LOCAL_PATHS", "true")
    monkeypatch.setenv("CODEINTEL_LLM", "none")
    monkeypatch.setenv("CODEINTEL_EMBEDDINGS", "hash")
    monkeypatch.delenv("CODEINTEL_API_KEY", raising=False)
    return tmp_path


@pytest.fixture()
def sample_repo(env):
    dst = env / "sample"
    shutil.copytree(EXAMPLE, dst)
    return dst


@pytest.fixture()
def indexed(env, sample_repo):
    from codeintel import db
    from codeintel.services import repository_service as rs
    repo = db.create_repo("sample", "local")
    rs.ingest(repo.id, "local", str(sample_repo))
    assert db.get_repo(repo.id).status.value == "ready", db.get_repo(repo.id).error
    return repo.id