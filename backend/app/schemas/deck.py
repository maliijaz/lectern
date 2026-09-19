"""Slide deck schema.

Layouts are a closed set so the PPTX renderer can map each one onto a real placeholder
arrangement in the theme file. The model picks a layout; the renderer owns how it looks.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Citation, Figure, GenerationMeta, LearningObjective


class SlideLayout(StrEnum):
    TITLE = "title"  # deck opener
    SECTION = "section"  # divider between parts
    BULLETS = "bullets"  # heading + bullet list
    TWO_COLUMN = "two_column"  # two parallel lists, e.g. pros / cons
    IMAGE_TEXT = "image_text"  # figure beside supporting text
    DIAGRAM = "diagram"  # full-bleed generated diagram
    TABLE = "table"  # comparison or data table
    QUOTE = "quote"  # a single highlighted statement
    DEFINITION = "definition"  # a term and its meaning, given room to breathe
    EXAMPLE = "example"  # a worked example, step by step
    QUIZ = "quiz"  # in-lecture check for understanding
    SUMMARY = "summary"  # recap of the key points
    QUESTIONS = "questions"  # discussion prompts / closing slide


class SlideColumn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str = Field(default="", description="Column heading")
    bullets: list[str] = Field(default_factory=list, description="Points in this column")


class Slide(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layout: SlideLayout = Field(
        default=SlideLayout.BULLETS, description="Which slide arrangement to use"
    )
    heading: str = Field(default="", description="Slide title, under 70 characters")
    subheading: str = Field(default="", description="Optional second line")
    bullets: list[str] = Field(
        default_factory=list,
        description=(
            "Points for this slide. Each is a short phrase, not a sentence, under 15 words. "
            "Six at most — detail belongs in the speaker notes."
        ),
    )
    columns: list[SlideColumn] = Field(
        default_factory=list, description="Used only by the two_column layout"
    )
    table_markdown: str = Field(
        default="", description="Markdown table, used only by the table layout"
    )
    quote: str = Field(default="", description="Used by the quote layout")
    quote_attribution: str = Field(default="", description="Who said it")
    figure: Figure | None = Field(
        default=None, description="A diagram or chart to generate and place on the slide"
    )
    speaker_notes: str = Field(
        default="",
        description=(
            "What the teacher actually says: the explanation, the transition, and any "
            "question to pose. Two to five sentences."
        ),
    )
    duration_minutes: float = Field(
        default=2.0, ge=0, description="Estimated time to teach this slide"
    )
    citations: list[Citation] = Field(
        default_factory=list, description="Sources supporting this slide's content"
    )

    @model_validator(mode="after")
    def _fill_layout_gaps(self) -> Slide:
        """Repair the small inconsistencies models produce, rather than rejecting the deck.

        A slide labelled `two_column` with its content in `bullets` is a usable slide; it
        just needs reshaping. Failing validation here would waste a whole generation.
        """
        if self.layout == SlideLayout.TWO_COLUMN and not self.columns and self.bullets:
            midpoint = (len(self.bullets) + 1) // 2
            self.columns = [
                SlideColumn(bullets=self.bullets[:midpoint]),
                SlideColumn(bullets=self.bullets[midpoint:]),
            ]
            self.bullets = []
        if self.layout == SlideLayout.TABLE and not self.table_markdown and self.bullets:
            self.layout = SlideLayout.BULLETS
        if self.layout == SlideLayout.QUOTE and not self.quote:
            self.quote = self.bullets[0] if self.bullets else self.heading
        if self.layout == SlideLayout.DIAGRAM and (self.figure is None or not self.figure.spec):
            self.layout = SlideLayout.BULLETS
        return self


class Deck(BaseModel):
    """A complete presentation."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="Deck title")
    subtitle: str = Field(default="", description="Course, unit or date line")
    presenter: str = Field(default="", description="Teacher or institution name")
    objectives: list[LearningObjective] = Field(
        default_factory=list, description="What learners should be able to do afterwards"
    )
    slides: list[Slide] = Field(default_factory=list, description="Slides in teaching order")
    theme: str = Field(default="academic", description="Template name to render with")
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    @property
    def duration_minutes(self) -> float:
        return round(sum(s.duration_minutes for s in self.slides), 1)

    def outline(self) -> list[str]:
        return [f"{i + 1}. {s.heading or s.layout.value}" for i, s in enumerate(self.slides)]


class SlideOutlineItem(BaseModel):
    """One line of the planning pass, before any slide content is written.

    Planning the whole deck first is what stops the model from repeating itself on slide 9
    and running out of material by slide 12.
    """

    model_config = ConfigDict(extra="ignore")

    heading: str = Field(description="Working title for the slide")
    layout: SlideLayout = Field(default=SlideLayout.BULLETS)
    intent: str = Field(description="What this slide must accomplish, in one sentence")
    key_points: list[str] = Field(
        default_factory=list, description="Two to four points this slide will cover"
    )
    duration_minutes: float = Field(default=2.0, ge=0)


class DeckOutline(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    subtitle: str = ""
    objectives: list[LearningObjective] = Field(default_factory=list)
    slides: list[SlideOutlineItem] = Field(default_factory=list)
