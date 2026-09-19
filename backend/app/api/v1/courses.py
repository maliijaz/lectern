"""Courses: the optional grouping a teacher organises their year around.

Also the home of curriculum coverage — which syllabus outcomes the material generated so
far actually addresses, and which have been quietly skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import Artifact, BankQuestion, Course, Document
from app.db.session import get_db

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = ""
    grade_level: str = ""
    description: str = ""
    outcomes: list[str] = Field(
        default_factory=list, description="Syllabus outcomes to track coverage against"
    )


class CourseOut(BaseModel):
    id: str
    name: str
    subject: str
    grade_level: str
    description: str
    outcomes: list[Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.post("", response_model=CourseOut, status_code=201)
async def create_course(body: CourseIn, db: AsyncSession = Depends(get_db)) -> Course:
    course = Course(**body.model_dump())
    db.add(course)
    await db.flush()
    return course


@router.get("", response_model=list[CourseOut])
async def list_courses(db: AsyncSession = Depends(get_db)) -> list[Course]:
    return list((await db.execute(select(Course).order_by(Course.name))).scalars().all())


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(course_id: str, db: AsyncSession = Depends(get_db)) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise NotFoundError(f"No course {course_id}")
    return course


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: str, body: CourseIn, db: AsyncSession = Depends(get_db)
) -> Course:
    course = await db.get(Course, course_id)
    if course is None:
        raise NotFoundError(f"No course {course_id}")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    return course


@router.delete("/{course_id}", status_code=204)
async def delete_course(course_id: str, db: AsyncSession = Depends(get_db)) -> None:
    course = await db.get(Course, course_id)
    if course is None:
        raise NotFoundError(f"No course {course_id}")
    # Documents and artifacts survive; their course_id is nulled by the FK rule.
    await db.delete(course)


@router.get("/{course_id}/coverage")
async def coverage(course_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Which syllabus outcomes the generated material touches, and which it misses.

    Matching is by keyword overlap against question topics and artifact titles rather than
    anything cleverer: it is transparent, needs no model call, and a teacher can see at a
    glance why something matched. It is a prompt to look, not a verdict.
    """
    course = await db.get(Course, course_id)
    if course is None:
        raise NotFoundError(f"No course {course_id}")

    artifacts = (
        (await db.execute(select(Artifact).where(Artifact.course_id == course_id))).scalars().all()
    )
    questions = (
        (await db.execute(select(BankQuestion).where(BankQuestion.course_id == course_id)))
        .scalars()
        .all()
    )

    haystack: list[tuple[str, str]] = [(a.title, f"artifact:{a.id}") for a in artifacts]
    haystack += [(q.topic, f"question:{q.id}") for q in questions if q.topic]

    outcomes = []
    for outcome in course.outcomes or []:
        text = outcome if isinstance(outcome, str) else str(outcome.get("text", outcome))
        keywords = {w for w in _keywords(text) if len(w) > 3}
        matches = [
            reference
            for candidate, reference in haystack
            if keywords and len(keywords & _keywords(candidate)) >= max(1, len(keywords) // 3)
        ]
        outcomes.append(
            {
                "outcome": text,
                "covered": bool(matches),
                "match_count": len(matches),
                "matches": matches[:10],
            }
        )

    marks_by_bloom = dict(
        (
            await db.execute(
                select(BankQuestion.bloom, func.sum(BankQuestion.marks))
                .where(BankQuestion.course_id == course_id)
                .group_by(BankQuestion.bloom)
            )
        ).all()
    )

    return {
        "course_id": course_id,
        "artifacts": len(artifacts),
        "questions": len(questions),
        "outcomes": outcomes,
        "uncovered": [o["outcome"] for o in outcomes if not o["covered"]],
        "marks_by_bloom": {str(k): float(v) for k, v in marks_by_bloom.items() if k},
    }


@router.get("/{course_id}/summary")
async def summary(course_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    documents = (
        await db.execute(select(func.count(Document.id)).where(Document.course_id == course_id))
    ).scalar_one()
    by_kind = dict(
        (
            await db.execute(
                select(Artifact.kind, func.count(Artifact.id))
                .where(Artifact.course_id == course_id)
                .group_by(Artifact.kind)
            )
        ).all()
    )
    return {"documents": documents, "artifacts_by_kind": by_kind}


_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "their",
        "they",
        "will",
        "able",
        "students",
        "student",
        "learn",
        "learning",
        "understand",
        "explain",
        "describe",
        "identify",
        "about",
        "using",
        "into",
        "have",
        "been",
        "should",
    }
)


def _keywords(text: str) -> set[str]:
    return {
        word
        for word in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if word not in _STOPWORDS and len(word) > 2
    }
