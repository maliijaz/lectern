"""Vocabulary shared by every generated artifact."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Bloom(StrEnum):
    """Revised Bloom's taxonomy, low to high.

    Used to steer generation *and* to audit the result: an exam that is 90% `remember`
    is a bad exam, and the blueprint validator will say so.
    """

    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"
    EVALUATE = "evaluate"
    CREATE = "create"


BLOOM_ORDER: tuple[Bloom, ...] = (
    Bloom.REMEMBER,
    Bloom.UNDERSTAND,
    Bloom.APPLY,
    Bloom.ANALYZE,
    Bloom.EVALUATE,
    Bloom.CREATE,
)

#: Verbs that reliably signal each level — injected into prompts so the model writes
#: objectives that are actually measurable.
BLOOM_VERBS: dict[Bloom, tuple[str, ...]] = {
    Bloom.REMEMBER: ("define", "list", "recall", "name", "state", "identify"),
    Bloom.UNDERSTAND: ("explain", "describe", "summarise", "classify", "compare", "interpret"),
    Bloom.APPLY: ("solve", "calculate", "demonstrate", "use", "implement", "compute"),
    Bloom.ANALYZE: ("analyse", "differentiate", "examine", "contrast", "deduce", "organise"),
    Bloom.EVALUATE: ("justify", "critique", "assess", "defend", "judge", "recommend"),
    Bloom.CREATE: ("design", "construct", "compose", "formulate", "devise", "propose"),
}


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Depth(StrEnum):
    """How much prose a notes/worksheet generation should produce."""

    OUTLINE = "outline"
    STANDARD = "standard"
    DETAILED = "detailed"
    TEXTBOOK = "textbook"


class Audience(BaseModel):
    """Who the material is for. Steers vocabulary, examples and reading level."""

    model_config = ConfigDict(extra="ignore")

    grade_level: str = Field(
        default="", description="Year, grade or stage, e.g. 'Grade 9' or 'first-year undergraduate'"
    )
    subject: str = Field(default="", description="Subject or course name")
    prior_knowledge: str = Field(
        default="", description="What learners already know, so it is not re-explained"
    )
    language: str = Field(default="English", description="Language to write in")
    reading_level: str = Field(
        default="", description="Target reading level, e.g. 'Grade 7' — blank means match the grade"
    )


class Citation(BaseModel):
    """A pointer back to the source passage that supports a claim.

    Every generator that works from documents attaches these, which is what lets a teacher
    check the machine's work instead of trusting it.
    """

    model_config = ConfigDict(extra="ignore")

    document_id: str = Field(default="", description="Source document identifier")
    document_title: str = Field(default="", description="Human-readable source name")
    chunk_id: str = Field(default="", description="Identifier of the exact passage")
    page: int | None = Field(default=None, description="Page number in the source, if known")
    section: str = Field(default="", description="Heading the passage sat under")
    quote: str = Field(default="", description="Short supporting excerpt, under 200 characters")

    def label(self) -> str:
        parts = [self.document_title or self.document_id or "source"]
        if self.section:
            parts.append(self.section)
        if self.page is not None:
            parts.append(f"p.{self.page}")
        return " — ".join(p for p in parts if p)


class LearningObjective(BaseModel):
    """A measurable outcome. The spine of notes, lesson plans and exam blueprints."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(description="Starts with a measurable verb, e.g. 'Explain why ...'")
    bloom: Bloom = Field(default=Bloom.UNDERSTAND, description="Cognitive level targeted")
    topic: str = Field(default="", description="Topic or unit this objective belongs to")


class KeyTerm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    term: str
    definition: str
    example: str = Field(default="", description="A concrete example, if one helps")


class Figure(BaseModel):
    """A visual to be generated and placed. Never a URL to an external image —
    everything is produced locally so material works offline and has no licence risk."""

    model_config = ConfigDict(extra="ignore")

    kind: str = Field(
        default="diagram",
        description="One of: diagram, chart, table, equation, timeline, none",
    )
    caption: str = Field(default="", description="Caption shown under the figure")
    alt_text: str = Field(
        default="", description="Description for screen readers — always fill this"
    )
    #: Mermaid source for `diagram`, a chart spec for `chart`, markdown for `table`,
    #: LaTeX for `equation`. Interpreted by the renderer according to `kind`.
    spec: str = Field(default="", description="Source used to render the figure")


class GenerationMeta(BaseModel):
    """Provenance of a generated artifact, kept for reproducibility."""

    model_config = ConfigDict(extra="ignore")

    model: str = ""
    provider: str = ""
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    grounded: bool = False
    source_documents: list[str] = Field(default_factory=list)


def bloom_verb_hint(levels: list[Bloom] | None = None) -> str:
    """A compact prompt fragment listing acceptable verbs per level."""
    chosen = levels or list(BLOOM_ORDER)
    return "\n".join(f"- {b.value}: {', '.join(BLOOM_VERBS[b])}" for b in chosen)
