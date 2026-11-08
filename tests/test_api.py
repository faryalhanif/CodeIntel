import time

import pytest
from fastapi.testclient import TestClient

from codeintel.api.main import app


@pytest.fixture()
def client(env):
    return TestClient(app)


def wait(client, rid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/repositories/{rid}").json()
        if r["status"] in ("ready", "failed"):
            return r
        time.sleep(0.1)
    raise TimeoutError


def test_health(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and "security" in r["agents"]


def test_full_flow(client, sample_repo):
    r = client.post("/repositories", json={"local_path": str(sample_repo)})
    assert r.status_code == 202
    rid = r.json()["id"]
    assert wait(client, rid)["status"] == "ready"
    a = client.post(f"/repositories/{rid}/analysis", json={"task": "find security issues"})
    assert a.status_code == 202
    aid = a.json()["analysis_id"]
    for _ in range(100):
        got = client.get(f"/analyses/{aid}").json()
        if got["status"] != "running":
            break
        time.sleep(0.1)
    assert got["status"] == "done" and got["report"]["stats"]["total_findings"] > 0
    chat = client.post(f"/repositories/{rid}/chat", json={"question": "where is the token generated?"}).json()
    assert chat["citations"]
    assert client.get(f"/repositories/{rid}/overview").json()["data"]["counts"]["files"] > 0
    assert client.delete(f"/repositories/{rid}").status_code == 204
    assert client.get(f"/repositories/{rid}").status_code == 404


def test_validation_and_errors(client):
    assert client.post("/repositories", json={}).status_code == 422
    assert client.post("/repositories", json={"git_url": "http://github.com/a/b"}).status_code == 422
    assert client.get("/repositories/not-an-id").status_code == 400
    assert client.get("/repositories/aaaaaaaaaaaa").status_code == 404


def test_local_paths_disabled(client, monkeypatch, sample_repo):
    monkeypatch.setenv("CODEINTEL_ALLOW_LOCAL_PATHS", "false")
    assert client.post("/repositories", json={"local_path": str(sample_repo)}).status_code == 403


def test_api_key_enforced(client, monkeypatch):
    monkeypatch.setenv("CODEINTEL_API_KEY", "s3cret")
    assert client.get("/repositories").status_code == 401
    assert client.get("/repositories", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_zip_upload(client, sample_repo, tmp_path):
    import shutil
    z = shutil.make_archive(str(tmp_path / "proj"), "zip", sample_repo)
    with open(z, "rb") as fh:
        r = client.post("/repositories/upload", files={"file": ("proj.zip", fh, "application/zip")})
    assert r.status_code == 202
    assert wait(client, r.json()["id"])["status"] == "ready"
