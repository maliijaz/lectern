"""Lesson plan schema, shaped to fit the templates schools actually use."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Citation, GenerationMeta, LearningObjective


class PlanTemplate(StrEnum):
    GENERIC = "generic"  # objectives → activities → assessment
    FIVE_E = "5e"  # engage, explore, explain, elaborate, evaluate
    HUNTER = "hunter"  # Madeline Hunter's seven steps
    GRR = "gradual_release"  # I do, we do, you do
    INQUIRY = "inquiry"


class ActivityKind(StrEnum):
    STARTER = "starter"
    DIRECT_INSTRUCTION = "direct_instruction"
    GUIDED_PRACTICE = "guided_practice"
    INDEPENDENT_PRACTICE = "independent_practice"
    GROUP_WORK = "group_work"
    DISCUSSION = "discussion"
    DEMONSTRATION = "demonstration"
    ASSESSMENT = "assessment"
    PLENARY = "plenary"


class Activity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Short name for this part of the lesson")
    kind: ActivityKind = ActivityKind.DIRECT_INSTRUCTION
    minutes: int = Field(default=10, ge=0)
    teacher_does: str = Field(description="What the teacher does and says")
    students_do: str = Field(description="What students are doing at the same time")
    materials: list[str] = Field(default_factory=list)
    #: A question the teacher asks here to check understanding before moving on.
    check_for_understanding: str = Field(default="")
    grouping: str = Field(default="", description="whole class, pairs, groups of four, individual")


class Differentiation(BaseModel):
    """How the same lesson serves learners who are behind, on track, and ahead."""

    model_config = ConfigDict(extra="ignore")

    support: list[str] = Field(
        default_factory=list, description="Scaffolds for learners who need more help"
    )
    core: list[str] = Field(default_factory=list, description="What the majority will do")
    extension: list[str] = Field(
        default_factory=list, description="Stretch tasks for learners who finish early"
    )
    language_support: list[str] = Field(
        default_factory=list, description="Help for learners working in an additional language"
    )
    accessibility: list[str] = Field(
        default_factory=list, description="Adjustments for specific access needs"
    )


class LessonPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    template: PlanTemplate = PlanTemplate.GENERIC
    subject: str = ""
    grade_level: str = ""
    duration_minutes: int = Field(default=45, ge=0)
    date: str = ""

    objectives: list[LearningObjective] = Field(default_factory=list)
    success_criteria: list[str] = Field(
        default_factory=list, description="'I can ...' statements students can self-check against"
    )
    prerequisites: list[str] = Field(default_factory=list)
    key_vocabulary: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)

    hook: str = Field(default="", description="How the lesson opens and grabs attention")
    activities: list[Activity] = Field(default_factory=list)
    closure: str = Field(default="", description="How the lesson is wrapped up")

    formative_assessment: list[str] = Field(
        default_factory=list, description="How learning is checked during the lesson"
    )
    summative_assessment: str = Field(default="", description="Any graded task")
    homework: str = Field(default="")

    differentiation: Differentiation = Field(default_factory=Differentiation)
    anticipated_misconceptions: list[str] = Field(
        default_factory=list, description="What students commonly get wrong, and the fix"
    )
    teacher_notes: str = Field(default="")
    citations: list[Citation] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    @property
    def planned_minutes(self) -> int:
        return sum(a.minutes for a in self.activities)
