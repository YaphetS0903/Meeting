# ruff: noqa: F811 -- imported pytest fixture
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import get_settings
from app.services import integrations
from tests.test_admin_people import client  # noqa: F401


@pytest.fixture
def provider(db, team, monkeypatch):
    settings = get_settings()
    for key, value in [("wecom_corp_id", "corp"), ("wecom_agent_id", "1"), ("wecom_secret", "test")]:
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "public_url", "http://testserver")
    cache = {}
    redis = Mock()
    redis.setex = lambda key, ttl, value: cache.update({key: value})
    redis.getdel = lambda key: cache.pop(key, None)
    monkeypatch.setattr(integrations, "redis_client", lambda: redis)
    team["a"].wecom_id = "wecom-a"
    db.flush()
    monkeypatch.setattr(integrations.WeCom, "get", lambda *args, **kwargs: {"userid": "wecom-a"})
    return cache


@pytest.mark.parametrize(
    "destination", ["/tasks?id=task-123", "/verifications?request=request-123", "/meetings/meeting-123"]
)
def test_oauth_restores_bound_internal_destination_and_consumes_state_once(client, provider, destination):
    response = client.get("/api/auth/wecom/url", params={"return_to": destination})
    assert response.status_code == 200
    params = parse_qs(urlparse(response.json()["url"]).query)
    state = params["state"][0]
    assert params["redirect_uri"] == ["http://testserver/tasks"]
    assert "httponly" in response.headers["set-cookie"].lower()
    exchange = client.post("/api/auth/wecom/exchange", json={"code": "fake-code", "state": state})
    assert exchange.status_code == 200 and exchange.json()["return_to"] == destination
    assert "access_token" in exchange.json() and provider == {}
    assert (
        client.post("/api/auth/wecom/exchange", json={"code": "fake-code", "state": state}).status_code == 401
    )


@pytest.mark.parametrize(
    "destination",
    [
        "https://outside.invalid/tasks",
        "https://[invalid/tasks",
        "//outside.invalid/tasks",
        "/\\outside.invalid",
        "/%2f%2foutside.invalid",
        "/tasks#https://outside.invalid",
        "/tasks?id=one&id=two",
        "/verifications?request=javascript:alert(1)",
    ],
)
def test_oauth_rejects_external_or_ambiguous_destinations(client, provider, destination):
    assert client.get("/api/auth/wecom/url", params={"return_to": destination}).status_code == 422
    assert provider == {}
