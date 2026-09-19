"""The shared-secret gate.

Lectern is meant to be exposed sometimes - over a Cloudflare tunnel from a machine with a
GPU, or on a free hosting tier as a demo. Both give out a public URL, and behind it is
either someone's GPU or their API quota. These tests pin the two halves that matter: the
gate is genuinely off by default, and genuinely closed when it is on.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import get_settings
from app.core.access import COOKIE, HEADER, QUERY_PARAM
from app.main import create_app

KEY = "a-shared-secret"


@pytest.fixture
def gated(monkeypatch) -> TestClient:
    monkeypatch.setenv("LECTERN_ACCESS_KEY", KEY)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


def test_no_key_configured_leaves_the_app_open(client) -> None:
    """The default is localhost, where a password is friction with nothing behind it."""
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/settings").status_code == 200


def test_the_api_is_refused_without_the_key(gated: TestClient) -> None:
    response = gated.get("/api/v1/settings")

    assert response.status_code == 401
    assert response.json()["code"] == "access_denied"


def test_the_header_is_accepted(gated: TestClient) -> None:
    response = gated.get("/api/v1/settings", headers={HEADER: KEY})

    assert response.status_code == 200


def test_a_wrong_key_is_refused(gated: TestClient) -> None:
    assert gated.get("/api/v1/settings", headers={HEADER: "nearly-right"}).status_code == 401


def test_health_answers_without_the_key(gated: TestClient) -> None:
    """A platform that cannot probe /health concludes the container is dead."""
    assert gated.get("/health").status_code == 200


def test_a_link_with_the_key_sets_a_cookie_and_drops_it_from_the_url(
    gated: TestClient,
) -> None:
    """The secret should not stay in history, bookmarks or a Referer header."""
    response = gated.get(f"/?{QUERY_PARAM}={KEY}", follow_redirects=False)

    assert response.status_code == 303
    assert QUERY_PARAM not in response.headers["location"]
    assert response.cookies[COOKIE] == KEY


def test_the_cookie_then_carries_the_session(gated: TestClient) -> None:
    gated.get(f"/?{QUERY_PARAM}={KEY}")  # follow the redirect, keeping the cookie

    assert gated.get("/api/v1/settings").status_code == 200


def test_a_browser_gets_a_readable_page_not_json(gated: TestClient) -> None:
    response = gated.get("/", headers={"accept": "text/html"})

    assert response.status_code == 401
    assert "text/html" in response.headers["content-type"]
    assert QUERY_PARAM in response.text


def test_the_key_is_never_echoed_back(gated: TestClient) -> None:
    """It is in SECRET_FIELDS; the settings endpoint must redact it like the LLM key."""
    body = gated.get("/api/v1/settings", headers={HEADER: KEY}).text

    assert KEY not in body
