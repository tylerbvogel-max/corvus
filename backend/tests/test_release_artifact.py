"""Release proof must exercise the packaged layout and exact image identity."""
import asyncio
import importlib.util
import io
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.composition.factory import _mount_frontend
from app.config import settings


def _script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_frontend_serves_root_assets_and_preserves_api_absence(tmp_path, monkeypatch):
    dist = tmp_path / "packaged" / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script type="module" src="/assets/main.js"></script>')
    (dist / "assets" / "main.js").write_text("export const ready = true;")
    monkeypatch.setattr(settings, "corvus_frontend_dist", str(dist))
    app = FastAPI()
    _mount_frontend(app)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            root = await client.get("/")
            assert root.status_code == 200 and 'type="module"' in root.text
            asset = await client.get("/assets/main.js")
            assert asset.status_code == 200 and "ready" in asset.text
            assert (await client.get("/assets/missing.js")).status_code == 404
            assert (await client.get("/recall/missing")).status_code == 404
            assert (await client.get("/operator-view")).status_code == 200
    asyncio.run(exercise())


def test_explicit_missing_frontend_fails_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "corvus_frontend_dist", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="CORVUS_FRONTEND_DIST has no index.html"):
        _mount_frontend(FastAPI())


def test_static_symlink_cannot_escape_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("portfolio")
    outside = tmp_path / "private.txt"
    outside.write_text("synthetic private data")
    (dist / "escape.txt").symlink_to(outside)
    monkeypatch.setattr(settings, "corvus_frontend_dist", str(dist))
    app = FastAPI()
    _mount_frontend(app)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/escape.txt")).status_code == 404
    asyncio.run(exercise())


class Resource(io.BytesIO):
    def __init__(self, body, content_type):
        super().__init__(body)
        self.status = 200
        self.headers = Message()
        self.headers['Content-Type'] = content_type


@pytest.mark.parametrize("asset_type,expected", [("text/javascript", True), ("text/html", False)])
def test_deployment_rejects_spa_fallback_for_script(monkeypatch, asset_type, expected):
    verifier = _script("verify_deployment")
    responses = iter([
        Resource(b'<script type="module" src="/assets/main.js"></script>', "text/html"),
        Resource(b"content", asset_type),
    ])
    monkeypatch.setattr(verifier.urllib.request, "urlopen", lambda *a, **kw: next(responses))
    assert verifier.check_frontend("http://test", 1)[0] is expected


def test_source_match_is_insufficient_when_image_differs(monkeypatch):
    verifier = _script("verify_deployment")
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="sha256:" + "a" * 64, stderr=""))
    assert not verifier.check_image_identity("container", "sha256:" + "b" * 64)[0]
    assert verifier.check_image_identity("container", "sha256:" + "a" * 64)[0]
