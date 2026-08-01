"""Proposal-router contract regressions."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.routers.proposals import router


pytestmark = pytest.mark.integration


def test_whoami_static_route_is_not_swallowed_by_proposal_id(monkeypatch):
    monkeypatch.setattr(settings, "rbac_mode", "disabled")
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get(
        "/admin/proposals/whoami",
        headers={"X-Corvus-User": "tyler"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "tyler", "role": "admin", "source": "disabled",
    }
