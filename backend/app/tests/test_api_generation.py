"""End-to-end API tests: generate → edit → export → download.

The whole path runs against the fake provider, so this is the test that would catch a
break anywhere between the HTTP layer and a file on disk.
"""

from __future__ import annotations

import time

import pytest

from app.llm import registry
from app.llm.fake import FakeProvider
from app.tests.test_generators import (
    _note_section_responder,
    _notes_closing_responder,
    _notes_outline_responder,
    _outline_responder,
    _question_batch_responder,
    _slide_batch_responder,
    _topic_responder,
)

RESPONDERS = {
    "DeckOutline": _outline_responder(8),
    "SlideBatch": _slide_batch_responder,
    "NotesOutline": _notes_outline_responder,
    "NoteSection": _note_section_responder,
    "LectureNotes": _notes_closing_responder,
    "TopicList": _topic_responder,
    "QuestionBatch": _question_batch_responder,
}


@pytest.fixture
def wired_client(client, monkeypatch):
    """A TestClient whose provider is a FakeProvider with realistic responders."""
    provider = FakeProvider(responders=RESPONDERS)
    monkeypatch.setattr(registry, "build_provider", lambda *a, **k: provider)
    # generation_service imported build_provider by name, so patch it there too.
    from app.services import generation_service

    monkeypatch.setattr(generation_service, "build_provider", lambda *a, **k: provider)
    return client


def _wait_for_job(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish: {job}")


# --------------------------------------------------------------------------- catalogue


def test_kinds_expose_schemas_and_formats(client) -> None:
    kinds = client.get("/api/v1/artifacts/kinds").json()
    by_kind = {k["kind"]: k for k in kinds}

    assert {
        "slides",
        "notes",
        "exam",
        "lesson_plan",
        "rubric",
        "worksheet",
        "flashcards",
        "grading",
    } <= set(by_kind)

    slides = by_kind["slides"]
    assert slides["request_schema"]["properties"]["slide_count"]
    assert any(f["key"] == "pptx" for f in slides["formats"])
    # PowerPoint is not offered for a question paper.
    assert not any(f["key"] == "pptx" for f in by_kind["exam"]["formats"])
    # Moodle export is offered for question papers.
    assert any(f["key"] == "moodle_xml" for f in by_kind["exam"]["formats"])


def test_themes_are_listed(client) -> None:
    themes = client.get("/api/v1/artifacts/themes").json()
    assert {t["key"] for t in themes} >= {"academic", "minimal", "chalkboard"}


# --------------------------------------------------------------------------- slides


def test_generate_slides_end_to_end(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={
            "kind": "slides",
            "params": {
                "topic": "Photosynthesis",
                "slide_count": 8,
                "audience": {"grade_level": "Grade 10", "subject": "Biology"},
            },
            "export_formats": ["pptx", "md"],
        },
    )
    assert response.status_code == 202, response.text
    artifact_id = response.json()["artifact"]["id"]
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")

    detail = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    assert detail["status"] == "ready"
    assert detail["content"]["slides"]
    assert detail["content"]["title"] == "Photosynthesis"

    formats = {f["fmt"] for f in detail["files"]}
    assert {"pptx", "md"} <= formats
    assert all(not f["stale"] for f in detail["files"])

    pptx_file = next(f for f in detail["files"] if f["fmt"] == "pptx")
    download = client.get(f"/api/v1/artifacts/{artifact_id}/files/{pptx_file['id']}/download")
    assert download.status_code == 200
    assert download.content[:2] == b"PK"  # a real Office zip container
    assert len(download.content) > 20_000


# --------------------------------------------------------------------------- exams


def test_generate_exam_and_export_every_format(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={
            "kind": "exam",
            "params": {
                "topic": "Photosynthesis",
                "total_marks": 40,
                "duration_minutes": 60,
                "exam_name": "Unit Test 1",
                "question_types": ["mcq", "short_answer", "long_answer"],
            },
            "export_formats": [],
        },
    )
    artifact_id = response.json()["artifact"]["id"]
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")

    report = job["result"]["report"]
    assert "blueprint_matches" in report
    assert report["actual_marks"] > 0

    exported = client.post(
        f"/api/v1/artifacts/{artifact_id}/export",
        json={"formats": ["docx", "pdf", "moodle_xml", "gift", "qti", "csv", "forms_csv"]},
    )
    assert exported.status_code == 200, exported.text
    files = exported.json()

    by_format: dict[str, list] = {}
    for record in files:
        by_format.setdefault(record["fmt"], []).append(record)

    assert set(by_format) == {"docx", "pdf", "moodle_xml", "gift", "qti", "csv", "forms_csv"}
    # Word and PDF each produce a paper plus an answer key.
    assert {f["role"] for f in by_format["docx"]} == {"main", "answer_key"}
    assert {f["role"] for f in by_format["pdf"]} == {"main", "answer_key"}
    assert all(f["size_bytes"] > 0 for f in files)

    moodle = by_format["moodle_xml"][0]
    content = client.get(
        f"/api/v1/artifacts/{artifact_id}/files/{moodle['id']}/download"
    ).content.decode()
    assert content.startswith("<?xml")
    assert "<quiz>" in content


def test_generated_questions_are_banked(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={
            "kind": "exam",
            "params": {"topic": "Cells", "total_marks": 20},
            "export_formats": [],
        },
    )
    _wait_for_job(client, response.json()["job_id"])

    bank = client.get("/api/v1/question-bank").json()
    assert bank
    assert all(q["stem"] for q in bank)

    facets = client.get("/api/v1/question-bank/facets").json()
    assert facets["total"] >= len(bank)
    assert facets["types"]


# --------------------------------------------------------------------------- editing


def test_editing_content_marks_exports_stale(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={"kind": "notes", "params": {"topic": "Photosynthesis"}, "export_formats": ["md"]},
    )
    artifact_id = response.json()["artifact"]["id"]
    _wait_for_job(client, response.json()["job_id"])

    detail = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    assert detail["version"] == 1
    assert all(not f["stale"] for f in detail["files"])

    content = detail["content"]
    content["title"] = "Photosynthesis — revised"
    updated = client.put(
        f"/api/v1/artifacts/{artifact_id}/content",
        json={"content": content, "title": "Photosynthesis — revised"},
    ).json()

    assert updated["version"] == 2
    assert updated["title"] == "Photosynthesis — revised"
    assert all(f["stale"] for f in updated["files"])

    # Re-exporting clears the stale flag.
    refreshed = client.post(
        f"/api/v1/artifacts/{artifact_id}/export", json={"formats": ["md"]}
    ).json()
    assert all(not f["stale"] for f in refreshed)


def test_invalid_edit_is_rejected(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={
            "kind": "exam",
            "params": {"topic": "Cells", "total_marks": 10},
            "export_formats": [],
        },
    )
    artifact_id = response.json()["artifact"]["id"]
    _wait_for_job(client, response.json()["job_id"])

    detail = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    content = detail["content"]
    # Strip the correct answer from an MCQ — the schema must refuse this.
    for section in content["sections"]:
        for question in section["questions"]:
            if question["type"] == "mcq":
                for option in question["options"]:
                    option["is_correct"] = False

    rejected = client.put(f"/api/v1/artifacts/{artifact_id}/content", json={"content": content})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "validation_failed"


# --------------------------------------------------------------------------- library


def test_library_listing_and_filtering(wired_client) -> None:
    client = wired_client
    for kind, params in (
        ("notes", {"topic": "Cells"}),
        ("flashcards", {"topic": "Cells", "card_count": 5}),
    ):
        response = client.post(
            "/api/v1/artifacts",
            json={"kind": kind, "params": params, "export_formats": []},
        )
        _wait_for_job(client, response.json()["job_id"])

    everything = client.get("/api/v1/artifacts").json()
    assert len(everything) >= 2

    notes_only = client.get("/api/v1/artifacts", params={"kind": "notes"}).json()
    assert notes_only
    assert all(a["kind"] == "notes" for a in notes_only)

    # Search against a title that actually exists rather than the request topic — the
    # generator titles an artifact from the model's output, not from the prompt.
    needle = everything[0]["title"][:8]
    searched = client.get("/api/v1/artifacts", params={"q": needle}).json()
    assert searched
    assert all(needle.lower() in a["title"].lower() for a in searched)


def test_delete_removes_artifact_and_files(wired_client) -> None:
    client = wired_client
    response = client.post(
        "/api/v1/artifacts",
        json={"kind": "notes", "params": {"topic": "Cells"}, "export_formats": ["md"]},
    )
    artifact_id = response.json()["artifact"]["id"]
    _wait_for_job(client, response.json()["job_id"])

    detail = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    assert detail["files"]

    assert client.delete(f"/api/v1/artifacts/{artifact_id}").status_code == 204
    assert client.get(f"/api/v1/artifacts/{artifact_id}").status_code == 404


def test_failed_generation_is_visible_in_the_library(client, monkeypatch) -> None:
    """A generation that fails must leave a `failed` artifact carrying the reason.

    The failure write happens while the surrounding transaction is being rolled back, so
    without a separate transaction the artifact would sit on `generating` forever.
    """
    from app.llm import registry
    from app.services import generation_service

    # A provider that always returns something the schema rejects, so the repair loop
    # exhausts its budget — the real-world failure this guards against.
    broken = FakeProvider(scripted=["not json at all"] * 12)
    monkeypatch.setattr(registry, "build_provider", lambda *a, **k: broken)
    monkeypatch.setattr(generation_service, "build_provider", lambda *a, **k: broken)

    response = client.post(
        "/api/v1/artifacts",
        json={"kind": "notes", "params": {"topic": "Anything"}, "export_formats": []},
    )
    artifact_id = response.json()["artifact"]["id"]
    job = _wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "failed"

    artifact = client.get(f"/api/v1/artifacts/{artifact_id}").json()
    assert artifact["status"] == "failed"
    assert artifact["error"], "the artifact must carry the reason it failed"


def test_bad_request_is_rejected_before_a_job_is_queued(client) -> None:
    """A request with neither a topic nor documents must fail fast, not burn a job."""
    response = client.post(
        "/api/v1/artifacts", json={"kind": "slides", "params": {"slide_count": 10}}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --------------------------------------------------------------------------- courses


def test_course_coverage_reports_uncovered_outcomes(wired_client) -> None:
    client = wired_client
    course = client.post(
        "/api/v1/courses",
        json={
            "name": "Biology 10",
            "subject": "Biology",
            "outcomes": [
                "Explain the light-dependent reactions of photosynthesis",
                "Describe the structure of the mammalian heart",
            ],
        },
    ).json()

    response = client.post(
        "/api/v1/artifacts",
        json={
            "kind": "exam",
            "params": {
                "topic": "Photosynthesis light reactions",
                "total_marks": 20,
                "course_id": course["id"],
            },
            "export_formats": [],
        },
    )
    _wait_for_job(client, response.json()["job_id"])

    coverage = client.get(f"/api/v1/courses/{course['id']}/coverage").json()
    assert coverage["questions"] > 0
    assert len(coverage["outcomes"]) == 2
    # The heart outcome has nothing addressing it.
    assert any("heart" in outcome.lower() for outcome in coverage["uncovered"])
