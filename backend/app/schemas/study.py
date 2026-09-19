"""Worksheets, flashcards and the grading assistant's output."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Citation, Difficulty, Figure, GenerationMeta, LearningObjective
from app.schemas.paper import Question

# --------------------------------------------------------------------------- worksheets


class WorksheetSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    instructions: str = Field(default="", description="What the student should do here")
    questions: list[Question] = Field(default_factory=list)
    #: Blank lines printed after each question for handwritten answers.
    answer_lines: int = Field(default=0, ge=0)


class Worksheet(BaseModel):
    """Practice material. Unlike an exam, questions are ordered easy → hard so a student
    working alone builds confidence before hitting the difficult items."""

    model_config = ConfigDict(extra="ignore")

    title: str
    subject: str = ""
    grade_level: str = ""
    objectives: list[LearningObjective] = Field(default_factory=list)
    warm_up: list[str] = Field(
        default_factory=list, description="Quick recall prompts to activate prior knowledge"
    )
    worked_example: str = Field(
        default="", description="A solved example at the top, modelling the method"
    )
    sections: list[WorksheetSection] = Field(default_factory=list)
    challenge: list[Question] = Field(
        default_factory=list, description="Stretch questions for early finishers"
    )
    reflection_prompt: str = Field(
        default="", description="A closing question about the student's own learning"
    )
    estimated_minutes: int = Field(default=20, ge=0)
    citations: list[Citation] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    @property
    def questions(self) -> list[Question]:
        return [q for s in self.sections for q in s.questions] + self.challenge


# --------------------------------------------------------------------------- flashcards


class CardKind(StrEnum):
    BASIC = "basic"  # front → back
    REVERSED = "reversed"  # generates both directions
    CLOZE = "cloze"  # fill the gap in a sentence


class Flashcard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: CardKind = CardKind.BASIC
    front: str = Field(description="Prompt side. For cloze, the sentence with {{c1::gaps}}")
    back: str = Field(default="", description="Answer side")
    hint: str = Field(default="")
    example: str = Field(default="", description="A sentence or case showing the term in use")
    tags: list[str] = Field(default_factory=list)
    difficulty: Difficulty = Difficulty.MEDIUM
    figure: Figure | None = None
    citation: Citation | None = None


class FlashcardDeck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    description: str = ""
    subject: str = ""
    cards: list[Flashcard] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)


# --------------------------------------------------------------------------- grading


class CriterionScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterion: str
    level: str = Field(default="", description="Performance level awarded")
    points: float = Field(default=0, ge=0)
    max_points: float = Field(default=0, ge=0)
    justification: str = Field(
        description="The specific evidence in the student's work that earned this level"
    )
    evidence_quote: str = Field(
        default="", description="A short quote from the student's work supporting the judgement"
    )


class GradingResult(BaseModel):
    """Assistive marking. Always shows its reasoning: the teacher decides, not the model.

    `confidence` and `needs_teacher_review` exist so borderline work is flagged rather
    than quietly scored.
    """

    model_config = ConfigDict(extra="ignore")

    student_identifier: str = Field(default="", description="Name or ID, if supplied")
    task_title: str = Field(default="")
    scores: list[CriterionScore] = Field(default_factory=list)
    total_points: float = Field(default=0, ge=0)
    max_points: float = Field(default=0, ge=0)
    grade: str = Field(default="", description="Letter or band, if the scale calls for one")

    strengths: list[str] = Field(default_factory=list)
    areas_to_improve: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(
        default_factory=list, description="Concrete actions the student can take next"
    )
    feedback_to_student: str = Field(
        default="", description="Feedback written directly to the student, warm and specific"
    )

    confidence: float = Field(
        default=0.5, ge=0, le=1, description="How confident the model is in this marking"
    )
    needs_teacher_review: bool = Field(
        default=True, description="True whenever the work is borderline or ambiguous"
    )
    review_reason: str = Field(default="", description="Why a human should look at this")
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    @property
    def percentage(self) -> float:
        return round(100 * self.total_points / self.max_points, 1) if self.max_points else 0.0
