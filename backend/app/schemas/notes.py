"""Lecture notes schema.

Prose lives in `body` as Markdown — the one place the model is allowed to write markup,
because every renderer (DOCX, PDF, HTML) can consume it and a teacher can edit it directly.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Citation, Depth, Figure, GenerationMeta, KeyTerm, LearningObjective


class CalloutKind(StrEnum):
    NOTE = "note"
    TIP = "tip"
    WARNING = "warning"
    MISCONCEPTION = "misconception"  # a wrong idea learners commonly hold
    EXAM_TIP = "exam_tip"
    REAL_WORLD = "real_world"


class Callout(BaseModel):
    """A boxed aside. `misconception` is the highest-value one: naming what students
    typically get wrong is the difference between notes and a textbook dump."""

    model_config = ConfigDict(extra="ignore")

    kind: CalloutKind = CalloutKind.NOTE
    title: str = Field(default="", description="Short label for the box")
    body: str = Field(description="One or two sentences")


class WorkedExample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str = Field(description="The problem or scenario")
    steps: list[str] = Field(default_factory=list, description="Each step, in order")
    answer: str = Field(default="", description="The result")
    commentary: str = Field(default="", description="Why the method works")


class NoteSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str = Field(description="Section heading")
    body: str = Field(
        default="",
        description=(
            "The explanation, in Markdown. Use paragraphs, lists and **bold** for key "
            "terms. No headings — the section heading is already set."
        ),
    )
    key_terms: list[KeyTerm] = Field(default_factory=list)
    examples: list[WorkedExample] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)
    figures: list[Figure] = Field(default_factory=list)
    subsections: list[NoteSection] = Field(
        default_factory=list, description="Nested subsections, at most one level deep"
    )
    citations: list[Citation] = Field(default_factory=list)


NoteSection.model_rebuild()


class ReviewQuestion(BaseModel):
    """A check-your-understanding question at the end of the notes."""

    model_config = ConfigDict(extra="ignore")

    question: str
    answer: str = Field(default="", description="A model answer, brief")


class LectureNotes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    subtitle: str = Field(default="", description="Course, unit or date line")
    depth: Depth = Depth.STANDARD
    overview: str = Field(default="", description="A paragraph orienting the reader")
    objectives: list[LearningObjective] = Field(default_factory=list)
    prerequisites: list[str] = Field(
        default_factory=list, description="What a learner needs to know first"
    )
    sections: list[NoteSection] = Field(default_factory=list)
    summary: str = Field(default="", description="Closing recap of the key ideas")
    glossary: list[KeyTerm] = Field(
        default_factory=list, description="Every key term collected in one place"
    )
    review_questions: list[ReviewQuestion] = Field(default_factory=list)
    further_reading: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    def word_count(self) -> int:
        def count(section: NoteSection) -> int:
            total = len(section.body.split())
            total += sum(len(e.prompt.split()) + len(e.answer.split()) for e in section.examples)
            total += sum(count(s) for s in section.subsections)
            return total

        return len(self.overview.split()) + sum(count(s) for s in self.sections)


class NotesOutline(BaseModel):
    """Planning pass: the section structure before any prose is written."""

    model_config = ConfigDict(extra="ignore")

    title: str
    overview: str = ""
    objectives: list[LearningObjective] = Field(default_factory=list)
    section_plan: list[SectionPlan] = Field(default_factory=list)


class SectionPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str
    covers: str = Field(description="What this section must explain, in one sentence")
    key_points: list[str] = Field(default_factory=list)
    needs_example: bool = Field(
        default=False, description="True when a worked example would help here"
    )


NotesOutline.model_rebuild()
