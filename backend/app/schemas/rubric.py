"""Rubric schema — analytic (criteria × levels) or holistic (levels only)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import GenerationMeta


class RubricStyle(StrEnum):
    ANALYTIC = "analytic"  # a grid: every criterion scored separately
    HOLISTIC = "holistic"  # one overall judgement per level
    SINGLE_POINT = "single_point"  # the standard, with room for "not yet" and "exceeds"


class PerformanceLevel(BaseModel):
    """A column of the rubric grid."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="e.g. 'Exemplary', 'Proficient', 'Developing', 'Beginning'")
    #: Fraction of the criterion's points earned at this level, 0.0–1.0.
    weight: float = Field(default=1.0, ge=0, le=1)
    description: str = Field(default="", description="What work at this level looks like overall")


class CriterionLevel(BaseModel):
    """One cell of the grid: what this criterion looks like at this level."""

    model_config = ConfigDict(extra="ignore")

    level_name: str = Field(description="Must match a PerformanceLevel name")
    descriptor: str = Field(
        description=(
            "Observable, specific description of the work at this level. Describe what is "
            "present, not what is missing."
        )
    )


class Criterion(BaseModel):
    """A row of the rubric grid."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="What is being judged, e.g. 'Use of evidence'")
    description: str = Field(default="", description="What this criterion means")
    max_points: float = Field(default=4.0, ge=0)
    weight_percent: float = Field(
        default=0.0, ge=0, le=100, description="Share of the total grade; 0 means equal weighting"
    )
    levels: list[CriterionLevel] = Field(default_factory=list)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    task_description: str = Field(default="", description="The assignment being assessed")
    style: RubricStyle = RubricStyle.ANALYTIC
    grade_level: str = ""
    subject: str = ""

    levels: list[PerformanceLevel] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)

    #: For holistic rubrics: one descriptor per level instead of a grid.
    holistic_descriptors: list[CriterionLevel] = Field(default_factory=list)

    total_points: float = Field(default=0, ge=0)
    student_facing_summary: str = Field(
        default="",
        description="A plain-language version students can read before starting the task",
    )
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    @model_validator(mode="after")
    def _fill_totals(self) -> Rubric:
        if not self.total_points and self.criteria:
            self.total_points = sum(c.max_points for c in self.criteria)
        # Equal weighting when none was specified, so the renderer always has percentages.
        if self.criteria and all(c.weight_percent == 0 for c in self.criteria):
            share = round(100 / len(self.criteria), 2)
            for criterion in self.criteria:
                criterion.weight_percent = share
        return self

    def grid(self) -> list[list[str]]:
        """The rubric as rows of cells, ready for a table renderer."""
        header = ["Criterion", *(level.name for level in self.levels), "Points"]
        rows = [header]
        for criterion in self.criteria:
            by_level = {cell.level_name: cell.descriptor for cell in criterion.levels}
            rows.append(
                [
                    criterion.name,
                    *(by_level.get(level.name, "") for level in self.levels),
                    f"{criterion.max_points:g}",
                ]
            )
        return rows
