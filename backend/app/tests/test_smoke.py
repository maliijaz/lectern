"""Phase 0 smoke tests: the app boots, settings round-trip, and the job pipeline runs."""

from __future__ import annotations

import time


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_settings_read_exposes_editable_keys(client) -> None:
    body = client.get("/api/v1/settings").json()
    assert body["values"]["llm_provider"] == "fake"
    assert "llm_model" in body["editable"]
    # Database URL is deliberately not runtime-editable.
    assert "database_url" not in body["editable"]


def test_settings_override_round_trip(client) -> None:
    updated = client.put("/api/v1/settings", json={"values": {"llm_temperature": 0.9}}).json()
    assert updated["values"]["llm_temperature"] == 0.9
    assert "llm_temperature" in updated["overridden"]

    reset = client.post("/api/v1/settings/reset").json()
    assert reset["overridden"] == []


def test_settings_rejects_non_editable_key(client) -> None:
    response = client.put("/api/v1/settings", json={"values": {"database_url": "sqlite://x"}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_api_key_is_never_echoed_back(client) -> None:
    client.put("/api/v1/settings", json={"values": {"llm_api_key": "sk-secret-value"}})
    values = client.get("/api/v1/settings").json()["values"]
    assert values["llm_api_key"] == "********"
    client.post("/api/v1/settings/reset")


def test_probe_reports_fake_provider(client) -> None:
    body = client.post("/api/v1/settings/probe").json()
    assert body["ok"] is True
    assert body["provider"] == "fake"


def test_status_lists_capabilities(client) -> None:
    body = client.get("/api/v1/settings/status").json()
    assert body["database"] is True
    assert "pptx" in body["capabilities"]
    assert body["capabilities"]["pptx"]["available"] is True


def test_job_runs_to_completion(client) -> None:
    submitted = client.post(
        "/api/v1/jobs", json={"kind": "echo", "params": {"steps": 2, "delay": 0.01}}
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["id"]

    deadline = time.time() + 15
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.1)

    assert job["status"] == "succeeded", job
    assert job["progress"] == 1.0
    assert job["result"]["echoed"]["steps"] == 2


def test_unknown_job_kind_is_rejected(client) -> None:
    response = client.post("/api/v1/jobs", json={"kind": "does-not-exist", "params": {}})
    assert response.status_code == 404


def test_the_shipped_env_example_actually_boots(tmp_path) -> None:
    """The file a new user copies to .env must produce a working configuration.

    This caught a real failure: pydantic-settings JSON-decodes complex fields from a
    dotenv source before validators run, so the documented comma-separated
    LECTERN_CORS_ORIGINS raised a SettingsError and the app would not start at all. Anyone
    following the README hit it on their first run.
    """
    from pathlib import Path

    from app.config import PROJECT_ROOT, Settings

    example = PROJECT_ROOT / ".env.example"
    assert example.exists(), "the README tells people to copy this file"

    # Copy it exactly as a user would, and load from it.
    env_file = tmp_path / ".env"
    env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=str(env_file))

    assert settings.cors_origins, "CORS origins must parse from the comma-separated form"
    assert all(origin.startswith("http") for origin in settings.cors_origins)
    assert settings.llm_model
    assert isinstance(settings.data_dir, Path)


def test_comma_separated_origins_parse() -> None:
    from app.config import Settings

    settings = Settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_the_database_folder_is_created_if_missing(tmp_path, monkeypatch) -> None:
    """A hosting platform sets the database URL independently of the data directory.

    When that folder does not exist SQLite reports "unable to open database file", which
    tells you nothing. Creating it removes a confusing class of deployment failure.
    """
    from app.config import get_settings
    from app.db import session as session_module

    nested = tmp_path / "does" / "not" / "exist"
    assert not nested.exists()

    monkeypatch.setenv("LECTERN_DATABASE_URL", f"sqlite+aiosqlite:///{nested.as_posix()}/app.db")
    get_settings.cache_clear()
    try:
        session_module._ensure_sqlite_directory()
        assert nested.is_dir(), "the database's parent folder should have been created"
    finally:
        get_settings.cache_clear()


def test_directory_creation_ignores_non_sqlite_urls(monkeypatch) -> None:
    from app.config import get_settings
    from app.db import session as session_module

    monkeypatch.setenv("LECTERN_DATABASE_URL", "postgresql+asyncpg://user@host/db")
    get_settings.cache_clear()
    try:
        session_module._ensure_sqlite_directory()  # must simply do nothing
    finally:
        get_settings.cache_clear()
