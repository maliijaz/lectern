"""Generation requests — the API body, the CLI arguments and the generator input, all one
set of models so there is exactly one place a new option has to be added."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Audience, Bloom, Depth, Difficulty
from app.schemas.lesson import PlanTemplate
from app.schemas.paper import Blueprint, QuestionType
from app.schemas.rubric import RubricStyle


class BaseRequest(BaseModel):
    """Fields every generator accepts."""

    model_config = ConfigDict(extra="ignore")

    topic: str = Field(default="", description="Topic to generate from, when not using documents")
    document_ids: list[str] = Field(
        default_factory=list, description="Source documents to ground the output in"
    )
    audience: Audience = Field(default_factory=Audience)
    instructions: str = Field(
        default="", description="Extra direction from the teacher, in their own words"
    )
    course_id: str | None = None
    title: str = Field(default="", description="Override the generated title")

    @model_validator(mode="after")
    def _needs_a_source(self) -> BaseRequest:
        if not self.topic.strip() and not self.document_ids:
            raise ValueError("Provide a topic, one or more documents, or both")
        return self

    def subject_hint(self) -> str:
        return self.audience.subject or self.topic


class SlidesRequest(BaseRequest):
    slide_count: int = Field(default=12, ge=3, le=80, description="How many slides to produce")
    theme: str = Field(default="academic", description="Slide template to render with")
    lecture_minutes: int = Field(
        default=0, ge=0, description="Lecture length; when set, slide_count is derived from it"
    )
    include_objectives: bool = True
    include_summary: bool = True
    include_quiz_slides: bool = Field(
        default=True, description="Insert check-for-understanding slides through the deck"
    )
    include_diagrams: bool = Field(
        default=True, description="Let the model specify diagrams to be generated"
    )
    include_speaker_notes: bool = True
    presenter: str = ""

    @model_validator(mode="after")
    def _derive_slide_count(self) -> SlidesRequest:
        # About two minutes per slide is the rule of thumb that matches how people
        # actually teach; deriving it saves the teacher from guessing.
        if self.lecture_minutes:
            self.slide_count = max(3, min(80, round(self.lecture_minutes / 2)))
        return self


class NotesRequest(BaseRequest):
    depth: Depth = Depth.STANDARD
    include_examples: bool = True
    include_glossary: bool = True
    include_review_questions: bool = True
    include_misconceptions: bool = Field(
        default=True, description="Call out what students commonly get wrong"
    )
    max_sections: int = Field(default=8, ge=1, le=30)


class ExamRequest(BaseRequest):
    exam_name: str = Field(default="", description="e.g. 'Mid-Term Examination'")
    institution: str = ""
    total_marks: float = Field(default=50, gt=0)
    duration_minutes: int = Field(default=60, ge=5)
    date: str = ""

    question_types: list[QuestionType] = Field(
        default_factory=lambda: [
            QuestionType.MCQ,
            QuestionType.SHORT_ANSWER,
            QuestionType.LONG_ANSWER,
        ],
        description="Which kinds of question the paper may contain",
    )
    bloom_mix: dict[Bloom, float] = Field(
        default_factory=dict,
        description="Share of marks per Bloom level, e.g. {'remember': 0.3, 'apply': 0.4}",
    )
    difficulty_mix: dict[Difficulty, float] = Field(
        default_factory=lambda: {
            Difficulty.EASY: 0.3,
            Difficulty.MEDIUM: 0.5,
            Difficulty.HARD: 0.2,
        }
    )
    topics: list[str] = Field(
        default_factory=list, description="Topics to cover; derived from the source when empty"
    )
    blueprint: Blueprint | None = Field(
        default=None, description="An explicit table of specification, overriding the mixes"
    )

    variants: int = Field(default=1, ge=1, le=10, description="How many shuffled sets to produce")
    include_answer_key: bool = True
    verify_answers: bool = Field(
        default=True,
        description=(
            "Answer every machine-markable question a second time, blind, and flag any "
            "where the independent answer disagrees with the key. Adds about a minute."
        ),
    )
    include_explanations: bool = Field(default=True, description="Explain each answer in the key")
    instructions_list: list[str] = Field(
        default_factory=list, description="General instructions printed on the paper"
    )
    negative_marking: float = Field(
        default=0.0, ge=0, description="Marks deducted per wrong objective answer, 0 for none"
    )


class LessonPlanRequest(BaseRequest):
    template: PlanTemplate = PlanTemplate.GENERIC
    duration_minutes: int = Field(default=45, ge=5, le=480)
    class_size: int = Field(default=30, ge=1)
    available_materials: list[str] = Field(
        default_factory=list, description="What the room actually has, e.g. projector, lab kit"
    )
    include_differentiation: bool = True
    include_homework: bool = True
    date: str = ""


class RubricRequest(BaseRequest):
    task_description: str = Field(default="", description="The assignment being assessed")
    style: RubricStyle = RubricStyle.ANALYTIC
    criteria_count: int = Field(default=4, ge=2, le=12)
    level_names: list[str] = Field(
        default_factory=lambda: ["Exemplary", "Proficient", "Developing", "Beginning"]
    )
    total_points: float = Field(default=0, ge=0, description="0 lets the model choose")
    student_facing: bool = Field(
        default=True, description="Also write a plain-language version for students"
    )


class WorksheetRequest(BaseRequest):
    question_count: int = Field(default=12, ge=1, le=100)
    question_types: list[QuestionType] = Field(
        default_factory=lambda: [
            QuestionType.SHORT_ANSWER,
            QuestionType.MCQ,
            QuestionType.FILL_BLANK,
        ]
    )
    difficulty: Difficulty = Difficulty.MEDIUM
    graduated: bool = Field(
        default=True, description="Order questions easy to hard so students build confidence"
    )
    include_worked_example: bool = True
    include_challenge: bool = True
    answer_lines: int = Field(default=3, ge=0, le=20, description="Blank lines per question")
    estimated_minutes: int = Field(default=20, ge=0)


class FlashcardsRequest(BaseRequest):
    card_count: int = Field(default=25, ge=1, le=300)
    include_cloze: bool = Field(default=True, description="Also produce fill-the-gap cards")
    include_reversed: bool = Field(default=False, description="Generate both card directions")
    include_examples: bool = True


class GradingRequest(BaseModel):
    """Marking a single piece of student work against a rubric or a mark scheme."""

    model_config = ConfigDict(extra="ignore")

    student_work: str = Field(description="The student's answer, as text")
    student_identifier: str = Field(default="", description="Name or ID, optional")
    task_description: str = Field(default="", description="What was asked of the student")
    rubric_artifact_id: str | None = Field(
        default=None, description="An existing rubric artifact to mark against"
    )
    question_artifact_id: str | None = Field(
        default=None, description="A question paper whose mark scheme should be used"
    )
    max_points: float = Field(default=0, ge=0)
    strictness: str = Field(default="balanced", description="lenient, balanced or strict")
    feedback_tone: str = Field(default="encouraging", description="encouraging, neutral or direct")
    audience: Audience = Field(default_factory=Audience)


class AdaptRequest(BaseModel):
    """Rewriting existing content for a different audience."""

    model_config = ConfigDict(extra="ignore")

    artifact_id: str = Field(description="The artifact to adapt")
    target_reading_level: str = Field(default="", description="e.g. 'Grade 6'")
    target_language: str = Field(default="", description="Translate into this language")
    variant: str = Field(
        default="", description="support, core or extension — a differentiated version"
    )
    simplify_vocabulary: bool = False
    add_scaffolding: bool = Field(
        default=False, description="Add hints, sentence starters and worked steps"
    )
    preserve_structure: bool = Field(
        default=True, description="Keep the same sections and question count"
    )
