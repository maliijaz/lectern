"""SQLAlchemy models.

Two ideas drive this schema:

1. *Content and files are separate.* An ``Artifact`` holds the validated JSON the model
   produced; ``ArtifactFile`` rows are renderings of it. Editing the JSON and re-rendering
   never needs the model again.
2. *Provenance is kept.* ``DocumentChunk`` retains page and section for every chunk, so a
   generated bullet or exam question can point back at where it came from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# --------------------------------------------------------------------------- enums


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactKind(StrEnum):
    SLIDES = "slides"
    NOTES = "notes"
    EXAM = "exam"
    LESSON_PLAN = "lesson_plan"
    RUBRIC = "rubric"
    WORKSHEET = "worksheet"
    FLASHCARDS = "flashcards"
    GRADING = "grading"
    SUMMARY = "summary"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


# --------------------------------------------------------------------------- tables


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Course(Base, TimestampMixin):
    """Optional grouping — a class, subject or unit the teacher works in."""

    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(120), default="")
    grade_level: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Free-form syllabus outcomes used for curriculum coverage mapping.
    outcomes: Mapped[list[Any]] = mapped_column(JSON, default=list)

    documents: Mapped[list[Document]] = relationship(back_populates="course")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="course")


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )

    original_name: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500), default="")
    stored_path: Mapped[str] = mapped_column(String(1000))
    mime_type: Mapped[str] = mapped_column(String(200), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.UPLOADED, index=True)
    error: Mapped[str] = mapped_column(Text, default="")

    # Docling output, written next to the upload.
    markdown_path: Mapped[str] = mapped_column(String(1000), default="")
    docjson_path: Mapped[str] = mapped_column(String(1000), default="")

    page_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(20), default="")
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False)

    subject: Mapped[str] = mapped_column(String(120), default="")
    grade_level: Mapped[str] = mapped_column(String(60), default="")
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)

    course: Mapped[Course | None] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """A retrievable passage. The embedding itself lives in the vector store; this row is
    the durable copy of the text plus the provenance needed to cite it."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_doc_ordinal"),
        Index("ix_chunk_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str] = mapped_column(String(500), default="")
    section_path: Mapped[str] = mapped_column(String(1000), default="")
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    document: Mapped[Document] = relationship(back_populates="chunks")


class Artifact(Base, TimestampMixin):
    """A generated piece of teaching content, stored as validated JSON."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifact_kind_created", "kind", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )

    kind: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default=ArtifactStatus.DRAFT, index=True)
    error: Mapped[str] = mapped_column(Text, default="")

    # The request that produced it (kept so it can be re-run or tweaked).
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # The validated schema instance — a Deck, LectureNotes, QuestionPaper, ...
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    #: Quality audits produced during generation — the exam blueprint check and the
    #: independent answer-key verification. Stored on the artifact rather than left in the
    #: job result, because the teacher looks at the artifact, not at the job.
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    source_document_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    model_used: Mapped[str] = mapped_column(String(200), default="")
    generation_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    # Bumped on every save of `content`, so the UI can detect stale renders.
    version: Mapped[int] = mapped_column(Integer, default=1)

    course: Mapped[Course | None] = relationship(back_populates="artifacts")
    files: Mapped[list[ArtifactFile]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class ArtifactFile(Base):
    """One rendering of an artifact (pptx, docx, pdf, moodle xml, apkg, ...)."""

    __tablename__ = "artifact_files"
    __table_args__ = (Index("ix_file_artifact", "artifact_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    fmt: Mapped[str] = mapped_column(String(30))
    # e.g. "paper", "answer_key", "variant_B" — distinguishes multiple files of one format.
    role: Mapped[str] = mapped_column(String(50), default="main")
    filename: Mapped[str] = mapped_column(String(500))
    path: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Artifact.version this file was rendered from.
    source_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    artifact: Mapped[Artifact] = relationship(back_populates="files")


class BankQuestion(Base, TimestampMixin):
    """A reusable question. Every question generated for a paper is banked here so papers
    can be assembled from past work instead of always regenerating."""

    __tablename__ = "bank_questions"
    __table_args__ = (
        Index("ix_bank_filter", "qtype", "bloom", "difficulty"),
        Index("ix_bank_topic", "topic"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    qtype: Mapped[str] = mapped_column(String(30), index=True)
    topic: Mapped[str] = mapped_column(String(300), default="")
    bloom: Mapped[str] = mapped_column(String(20), default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="")
    marks: Mapped[float] = mapped_column(Float, default=1.0)
    # Searchable plain text of the stem.
    stem: Mapped[str] = mapped_column(Text, default="")
    # The full Question schema instance.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    times_used: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    """Background work. Polled and streamed over SSE by the UI."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_job_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED, index=True)

    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 .. 1.0
    message: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")

    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Runtime overrides of :class:`app.config.Settings`, editable from the UI."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"v": <any>}
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
