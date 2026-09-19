"""Question paper schema — the richest part of the product.

One design note worth stating plainly: rather than a discriminated union of eleven question
classes, this is a **single `Question` model with a `type` field and type-specific optional
fields**, enforced by a validator. Deep unions make JSON-Schema-constrained decoding brittle
on small local models, and they make the UI editor far harder to write. A flat model with a
strict validator gets the same guarantees with a much better hit rate.
"""

from __future__ import annotations

import random
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Bloom, Citation, Difficulty, Figure, GenerationMeta


class QuestionType(StrEnum):
    MCQ = "mcq"  # one correct option
    MULTI_SELECT = "multi_select"  # several correct options
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"  # a sentence or two
    LONG_ANSWER = "long_answer"  # structured answer or essay
    MATCHING = "matching"  # match column A to column B
    NUMERICAL = "numerical"  # compute a value
    CASE_STUDY = "case_study"  # scenario with sub-questions
    DIAGRAM_LABEL = "diagram_label"  # label a provided figure
    ASSERTION_REASON = "assertion_reason"  # common in competitive exams


#: Types a machine can mark without a human. Drives which formats can carry them
#: (Moodle/QTI/Forms) and which get auto-graded in the grading assistant.
AUTO_GRADABLE: frozenset[QuestionType] = frozenset(
    {
        QuestionType.MCQ,
        QuestionType.MULTI_SELECT,
        QuestionType.TRUE_FALSE,
        QuestionType.NUMERICAL,
        QuestionType.MATCHING,
        QuestionType.ASSERTION_REASON,
    }
)


class Option(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(description="The option as shown to the student")
    is_correct: bool = Field(default=False)
    rationale: str = Field(
        default="",
        description=(
            "Why this option is right, or — for a distractor — the specific misconception "
            "that would lead a student to choose it. This is what makes the paper "
            "diagnostic rather than just scored."
        ),
    )


class MatchPair(BaseModel):
    model_config = ConfigDict(extra="ignore")

    left: str = Field(description="Item in the first column")
    right: str = Field(description="Its match in the second column")


class RubricPoint(BaseModel):
    """One creditable element of a written answer."""

    model_config = ConfigDict(extra="ignore")

    description: str = Field(description="What the student must say or do")
    marks: float = Field(default=1.0, ge=0)


class SubQuestion(BaseModel):
    """A part of a case-study question. Deliberately not recursive."""

    model_config = ConfigDict(extra="ignore")

    label: str = Field(default="", description="Part label, e.g. 'a'")
    text: str
    marks: float = Field(default=1.0, ge=0)
    answer: str = Field(default="")
    options: list[Option] = Field(default_factory=list)


class Question(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: QuestionType = Field(description="Which kind of question this is")
    text: str = Field(description="The question stem, exactly as the student reads it")
    marks: float = Field(default=1.0, ge=0, description="Marks awarded")
    bloom: Bloom = Field(default=Bloom.UNDERSTAND)
    difficulty: Difficulty = Field(default=Difficulty.MEDIUM)
    topic: str = Field(default="", description="Topic or unit being assessed")
    estimated_minutes: float = Field(default=2.0, ge=0)

    # -- choice types --------------------------------------------------------
    options: list[Option] = Field(
        default_factory=list, description="For mcq, multi_select and assertion_reason"
    )

    # -- true/false ----------------------------------------------------------
    correct_bool: bool | None = Field(default=None, description="For true_false")

    # -- fill in the blanks --------------------------------------------------
    blanks: list[str] = Field(
        default_factory=list,
        description="Accepted answers, in order, for each ___ in the stem",
    )

    # -- written answers -----------------------------------------------------
    answer: str = Field(default="", description="Model answer for written question types")
    rubric_points: list[RubricPoint] = Field(
        default_factory=list, description="Mark scheme for written answers"
    )
    keywords: list[str] = Field(
        default_factory=list, description="Terms a correct short answer should contain"
    )

    # -- matching ------------------------------------------------------------
    pairs: list[MatchPair] = Field(default_factory=list, description="For matching questions")

    # -- numerical -----------------------------------------------------------
    numeric_answer: float | None = Field(default=None)
    unit: str = Field(default="", description="Unit of the numeric answer, e.g. 'm/s'")
    tolerance: float = Field(default=0.0, ge=0, description="Accepted absolute deviation")
    working: str = Field(default="", description="Worked solution, step by step")

    # -- case study ----------------------------------------------------------
    scenario: str = Field(default="", description="The stimulus a case_study question sets up")
    sub_questions: list[SubQuestion] = Field(default_factory=list)

    # -- diagram label -------------------------------------------------------
    figure: Figure | None = Field(default=None)
    labels: list[str] = Field(default_factory=list, description="Correct labels, in order")

    # -- shared --------------------------------------------------------------
    explanation: str = Field(
        default="", description="Why the answer is right — printed in the answer key"
    )
    hint: str = Field(default="", description="Optional scaffold for a support-level variant")
    citations: list[Citation] = Field(default_factory=list)

    #: Set when an independent pass disagreed with this question's answer key. A wrong key
    #: is the most damaging thing this product can produce, so the doubt travels with the
    #: question into the answer key, the editor and the exports.
    needs_review: bool = Field(
        default=False, description="True when the answer key for this question is disputed"
    )
    review_note: str = Field(default="", description="What the disagreement was")

    @model_validator(mode="after")
    def _check_type_requirements(self) -> Question:
        """Enforce what each type needs, repairing what is safely repairable.

        Repair beats rejection: a paper is expensive to regenerate, and a true/false item
        whose answer arrived as an option list is perfectly recoverable.
        """
        if self.type in (
            QuestionType.MCQ,
            QuestionType.MULTI_SELECT,
            QuestionType.ASSERTION_REASON,
        ):
            if len(self.options) < 2:
                raise ValueError(f"{self.type.value} needs at least 2 options")
            correct = [o for o in self.options if o.is_correct]
            if not correct:
                raise ValueError(f"{self.type.value} has no option marked is_correct")
            if self.type in (QuestionType.MCQ, QuestionType.ASSERTION_REASON) and len(correct) > 1:
                raise ValueError(
                    f"{self.type.value} must have exactly one correct option, found {len(correct)}"
                )

        elif self.type == QuestionType.TRUE_FALSE:
            if self.correct_bool is None:
                # The model sometimes answers a true/false as a two-option MCQ.
                for option in self.options:
                    if option.is_correct:
                        self.correct_bool = option.text.strip().lower().startswith("t")
                        break
            if self.correct_bool is None:
                raise ValueError("true_false needs correct_bool")
            self.options = []

        elif self.type == QuestionType.FILL_BLANK:
            if not self.blanks:
                raise ValueError("fill_blank needs at least one accepted answer in blanks")
            if "_" not in self.text:
                # No visible gap: append one per blank so the printed paper makes sense.
                self.text = self.text.rstrip(".") + " " + " ".join("____" for _ in self.blanks)

        elif self.type == QuestionType.MATCHING:
            if len(self.pairs) < 2:
                raise ValueError("matching needs at least 2 pairs")

        elif self.type == QuestionType.NUMERICAL:
            if self.numeric_answer is None:
                raise ValueError("numerical needs numeric_answer")
            if self.tolerance == 0 and self.numeric_answer:
                # A bare float comparison fails on rounding; allow 1% by default.
                self.tolerance = abs(self.numeric_answer) * 0.01

        elif self.type == QuestionType.CASE_STUDY:
            if not self.scenario:
                raise ValueError("case_study needs a scenario")
            if not self.sub_questions:
                raise ValueError("case_study needs at least one sub-question")
            total = sum(s.marks for s in self.sub_questions)
            if total > 0:
                self.marks = total  # parts are the source of truth for the mark total

        elif self.type in (QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER):
            if not self.answer and not self.rubric_points:
                raise ValueError(f"{self.type.value} needs an answer or a mark scheme")
            if self.type == QuestionType.LONG_ANSWER and not self.rubric_points and self.answer:
                self.rubric_points = [RubricPoint(description=self.answer, marks=self.marks)]

        elif self.type == QuestionType.DIAGRAM_LABEL:
            if not self.labels:
                raise ValueError("diagram_label needs the correct labels")

        return self

    # -- helpers used by renderers and the grader ----------------------------

    @property
    def auto_gradable(self) -> bool:
        return self.type in AUTO_GRADABLE

    def correct_options(self) -> list[Option]:
        return [o for o in self.options if o.is_correct]

    def answer_text(self) -> str:
        """A human-readable answer for the key, whatever the type."""
        match self.type:
            case QuestionType.MCQ | QuestionType.ASSERTION_REASON:
                correct = self.correct_options()
                return correct[0].text if correct else ""
            case QuestionType.MULTI_SELECT:
                return "; ".join(o.text for o in self.correct_options())
            case QuestionType.TRUE_FALSE:
                return "True" if self.correct_bool else "False"
            case QuestionType.FILL_BLANK:
                return ", ".join(self.blanks)
            case QuestionType.MATCHING:
                return "; ".join(f"{p.left} → {p.right}" for p in self.pairs)
            case QuestionType.NUMERICAL:
                value = f"{self.numeric_answer:g}" if self.numeric_answer is not None else ""
                return f"{value} {self.unit}".strip()
            case QuestionType.DIAGRAM_LABEL:
                return ", ".join(self.labels)
            case QuestionType.CASE_STUDY:
                return "\n".join(
                    f"({s.label or i + 1}) {s.answer}" for i, s in enumerate(self.sub_questions)
                )
            case _:
                return self.answer

    def shuffled(self, rng: random.Random) -> Question:
        """A copy with options and matching columns permuted, for exam variants."""
        clone = self.model_copy(deep=True)
        if clone.options:
            rng.shuffle(clone.options)
        if clone.pairs:
            # Shuffle only the right-hand column; the left keeps its order as the prompt.
            rights = [p.right for p in clone.pairs]
            rng.shuffle(rights)
            clone.pairs = [
                MatchPair(left=p.left, right=r) for p, r in zip(clone.pairs, rights, strict=True)
            ]
        return clone

    def option_letter(self, index: int) -> str:
        return chr(ord("A") + index)


class PaperSection(BaseModel):
    """A titled part of the paper, e.g. 'Section A — Objective Questions'."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Section heading")
    instructions: str = Field(default="", description="Directions specific to this section")
    questions: list[Question] = Field(default_factory=list)
    #: When set, students answer only this many — a common exam pattern ("answer any 3 of 5").
    choose_count: int | None = Field(
        default=None, ge=1, description="Answer any N of the questions listed"
    )

    @property
    def total_marks(self) -> float:
        if self.choose_count is not None and self.questions:
            highest = sorted((q.marks for q in self.questions), reverse=True)
            return sum(highest[: self.choose_count])
        return sum(q.marks for q in self.questions)


class BlueprintCell(BaseModel):
    """One requirement in the Table of Specification."""

    model_config = ConfigDict(extra="ignore")

    topic: str = Field(description="Topic or unit to assess")
    bloom: Bloom = Field(default=Bloom.UNDERSTAND)
    difficulty: Difficulty = Field(default=Difficulty.MEDIUM)
    question_type: QuestionType = Field(default=QuestionType.MCQ)
    count: int = Field(default=1, ge=1, description="How many questions of this kind")
    marks_each: float = Field(default=1.0, ge=0)

    @property
    def total_marks(self) -> float:
        return self.count * self.marks_each


class Blueprint(BaseModel):
    """The Table of Specification: what the paper must assess, before it is written.

    Generating against a blueprint — then checking the result back against it — is the
    difference between an exam and a pile of questions.
    """

    model_config = ConfigDict(extra="ignore")

    total_marks: float = Field(default=0, ge=0)
    duration_minutes: int = Field(default=60, ge=0)
    cells: list[BlueprintCell] = Field(default_factory=list)

    @property
    def planned_marks(self) -> float:
        return sum(c.total_marks for c in self.cells)

    @property
    def planned_questions(self) -> int:
        return sum(c.count for c in self.cells)

    def topics(self) -> list[str]:
        seen: dict[str, None] = {}
        for cell in self.cells:
            seen.setdefault(cell.topic, None)
        return list(seen)


class PaperMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    institution: str = Field(default="", description="School or university name")
    course: str = Field(default="", description="Course or subject")
    exam_name: str = Field(default="", description="e.g. 'Mid-Term Examination'")
    grade_level: str = Field(default="")
    duration_minutes: int = Field(default=60, ge=0)
    total_marks: float = Field(default=0, ge=0)
    date: str = Field(default="")
    instructions: list[str] = Field(
        default_factory=list, description="General instructions printed at the top"
    )
    variant_label: str = Field(default="", description="e.g. 'Set A' for multi-variant papers")


class QuestionPaper(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta: PaperMeta = Field(default_factory=PaperMeta)
    blueprint: Blueprint | None = None
    sections: list[PaperSection] = Field(default_factory=list)
    generation: GenerationMeta = Field(default_factory=GenerationMeta)

    @property
    def questions(self) -> list[Question]:
        return [q for section in self.sections for q in section.questions]

    @property
    def total_marks(self) -> float:
        return sum(s.total_marks for s in self.sections)

    @property
    def estimated_minutes(self) -> float:
        return round(sum(q.estimated_minutes for q in self.questions), 1)

    def variant(self, label: str, seed: int, *, shuffle_questions: bool = True) -> QuestionPaper:
        """A deterministic reshuffle of this paper — Set B, Set C and so on.

        Same questions, different order, so seating neighbours cannot copy. Section
        boundaries are preserved because sections usually carry different instructions.
        """
        rng = random.Random(seed)
        clone = self.model_copy(deep=True)
        clone.meta = clone.meta.model_copy(update={"variant_label": label})
        for section in clone.sections:
            section.questions = [q.shuffled(rng) for q in section.questions]
            if shuffle_questions:
                rng.shuffle(section.questions)
        return clone
