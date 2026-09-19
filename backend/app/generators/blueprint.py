"""Exam blueprints — the Table of Specification.

A blueprint says what a paper must assess before a single question is written: how many
marks on each topic, at which cognitive level, at which difficulty, in which question
format. This module does three things:

* **derive** a sensible blueprint from what a teacher actually specifies (total marks,
  a rough Bloom mix, a list of topics),
* **validate** a finished paper against it,
* **report** the gaps in language a teacher can act on.

Without this step an "exam generator" is a question generator, and the resulting paper is
80% recall questions on whatever the model found most memorable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.schemas.common import Bloom, Difficulty
from app.schemas.paper import Blueprint, BlueprintCell, QuestionPaper, QuestionType

log = get_logger(__name__)

#: A defensible default spread when the teacher does not specify one: weighted towards
#: understanding and application rather than recall, with a little higher-order work.
DEFAULT_BLOOM_MIX: dict[Bloom, float] = {
    Bloom.REMEMBER: 0.20,
    Bloom.UNDERSTAND: 0.30,
    Bloom.APPLY: 0.30,
    Bloom.ANALYZE: 0.15,
    Bloom.EVALUATE: 0.05,
}

#: Typical marks per question, used to turn a marks budget into a question count.
TYPICAL_MARKS: dict[QuestionType, float] = {
    QuestionType.MCQ: 1,
    QuestionType.MULTI_SELECT: 2,
    QuestionType.TRUE_FALSE: 1,
    QuestionType.FILL_BLANK: 1,
    QuestionType.MATCHING: 4,
    QuestionType.ASSERTION_REASON: 1,
    QuestionType.NUMERICAL: 3,
    QuestionType.SHORT_ANSWER: 3,
    QuestionType.LONG_ANSWER: 8,
    QuestionType.CASE_STUDY: 10,
    QuestionType.DIAGRAM_LABEL: 4,
}

#: Which question formats can honestly assess which cognitive levels. A multiple-choice
#: item cannot make a student *create* anything, and pretending otherwise is how papers
#: end up mislabelled.
SUITABLE_TYPES: dict[Bloom, tuple[QuestionType, ...]] = {
    Bloom.REMEMBER: (
        QuestionType.MCQ,
        QuestionType.TRUE_FALSE,
        QuestionType.FILL_BLANK,
        QuestionType.MATCHING,
    ),
    Bloom.UNDERSTAND: (
        QuestionType.MCQ,
        QuestionType.SHORT_ANSWER,
        QuestionType.ASSERTION_REASON,
        QuestionType.TRUE_FALSE,
    ),
    Bloom.APPLY: (
        QuestionType.NUMERICAL,
        QuestionType.SHORT_ANSWER,
        QuestionType.MCQ,
        QuestionType.CASE_STUDY,
    ),
    Bloom.ANALYZE: (
        QuestionType.LONG_ANSWER,
        QuestionType.CASE_STUDY,
        QuestionType.SHORT_ANSWER,
    ),
    Bloom.EVALUATE: (QuestionType.LONG_ANSWER, QuestionType.CASE_STUDY),
    Bloom.CREATE: (QuestionType.LONG_ANSWER, QuestionType.CASE_STUDY),
}


def derive(
    *,
    total_marks: float,
    duration_minutes: int,
    topics: list[str],
    question_types: list[QuestionType],
    bloom_mix: dict[Bloom, float] | None = None,
    difficulty_mix: dict[Difficulty, float] | None = None,
) -> Blueprint:
    """Build a blueprint from a teacher's high-level intent.

    Marks are split first across Bloom levels, then across topics, then assigned to the
    most suitable available question format. Rounding is reconciled at the end so the
    planned total matches the requested total exactly — a paper that says 50 marks and
    adds to 48 is worse than useless.
    """
    if not topics:
        topics = ["General"]
    if not question_types:
        question_types = [QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER]

    blooms = _normalise({k: v for k, v in (bloom_mix or DEFAULT_BLOOM_MIX).items() if v > 0})
    difficulties = _normalise({k: v for k, v in (difficulty_mix or {}).items() if v > 0}) or {
        Difficulty.EASY: 0.3,
        Difficulty.MEDIUM: 0.5,
        Difficulty.HARD: 0.2,
    }

    cells: list[BlueprintCell] = []
    for bloom, bloom_share in blooms.items():
        bloom_marks = total_marks * bloom_share
        qtype = _pick_type(bloom, question_types)
        marks_each = TYPICAL_MARKS.get(qtype, 2)

        for topic_index, topic in enumerate(topics):
            topic_marks = bloom_marks / len(topics)
            count = max(0, round(topic_marks / marks_each))
            if count == 0:
                # Too few marks for even one question of this type at this level: give the
                # first topic the remainder rather than dropping the level entirely.
                if topic_index == 0 and topic_marks >= marks_each * 0.5:
                    count = 1
                else:
                    continue
            cells.append(
                BlueprintCell(
                    topic=topic,
                    bloom=bloom,
                    difficulty=_difficulty_for(bloom, difficulties),
                    question_type=qtype,
                    count=count,
                    marks_each=marks_each,
                )
            )

    blueprint = Blueprint(total_marks=total_marks, duration_minutes=duration_minutes, cells=cells)
    _reconcile(blueprint, total_marks)
    return blueprint


def _normalise(mix: dict) -> dict:
    total = sum(mix.values())
    return {k: v / total for k, v in mix.items()} if total else {}


def _pick_type(bloom: Bloom, available: list[QuestionType]) -> QuestionType:
    """The best available format for this cognitive level."""
    for candidate in SUITABLE_TYPES.get(bloom, ()):
        if candidate in available:
            return candidate
    return available[0]


def _difficulty_for(bloom: Bloom, mix: dict[Difficulty, float]) -> Difficulty:
    """Higher cognitive levels skew harder — an `evaluate` question is rarely easy."""
    if bloom in (Bloom.REMEMBER, Bloom.UNDERSTAND):
        preference = (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD)
    elif bloom == Bloom.APPLY:
        preference = (Difficulty.MEDIUM, Difficulty.EASY, Difficulty.HARD)
    else:
        preference = (Difficulty.HARD, Difficulty.MEDIUM, Difficulty.EASY)
    for difficulty in preference:
        if mix.get(difficulty, 0) > 0:
            return difficulty
    return Difficulty.MEDIUM


def _reconcile(blueprint: Blueprint, target: float) -> None:
    """Adjust counts so the blueprint totals exactly ``target`` marks.

    Increments are spread across cells rather than piled onto whichever one happens to fit
    the shortfall best. Always topping up the same cell produces a paper with fifteen
    one-mark questions on one topic and nothing on the others — arithmetically correct and
    pedagogically useless.
    """
    if not blueprint.cells:
        return

    guard = 0
    while abs(blueprint.planned_marks - target) > 0.01 and guard < 500:
        guard += 1
        delta = target - blueprint.planned_marks

        if delta > 0:
            # Cells that fit within the shortfall, smallest count first so the extra marks
            # land where coverage is thinnest.
            candidates = [c for c in blueprint.cells if 0 < c.marks_each <= delta + 0.01]
            if not candidates:
                break
            min(candidates, key=lambda c: (c.count, c.marks_each)).count += 1
        else:
            # Take marks back from whichever cell is currently the fattest.
            reducible = [c for c in blueprint.cells if c.count > 1]
            if not reducible:
                break
            max(reducible, key=lambda c: (c.count, c.marks_each)).count -= 1

    # Any residue lands on the largest cell's per-question marks, keeping the total exact.
    residue = target - blueprint.planned_marks
    if abs(residue) > 0.01:
        cell = max(blueprint.cells, key=lambda c: c.count)
        cell.marks_each = max(0.5, round((cell.total_marks + residue) / cell.count, 2))


# --------------------------------------------------------------------------- validation


@dataclass
class BlueprintIssue:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class BlueprintReport:
    matches: bool
    actual_marks: float
    planned_marks: float
    issues: list[BlueprintIssue] = field(default_factory=list)
    bloom_actual: dict[str, float] = field(default_factory=dict)
    bloom_planned: dict[str, float] = field(default_factory=dict)
    topic_actual: dict[str, float] = field(default_factory=dict)
    topic_planned: dict[str, float] = field(default_factory=dict)

    @property
    def errors(self) -> list[str]:
        return [i.message for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[str]:
        return [i.message for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        if self.matches:
            return f"Paper matches the blueprint: {self.actual_marks:g} marks."
        return (
            f"Paper is {self.actual_marks:g} marks against a planned "
            f"{self.planned_marks:g}. " + "; ".join(self.errors[:3])
        )


def validate(
    paper: QuestionPaper, blueprint: Blueprint, *, tolerance: float = 0.15
) -> BlueprintReport:
    """Check a finished paper against its blueprint.

    ``tolerance`` is the allowed relative drift per Bloom level or topic before it is
    called an error — exact matching is not achievable and not worth demanding, but a
    paper that puts 60% of its marks on one unit is broken.
    """
    questions = paper.questions
    actual_total = paper.total_marks
    planned_total = blueprint.total_marks or blueprint.planned_marks

    issues: list[BlueprintIssue] = []

    if not questions:
        return BlueprintReport(
            matches=False,
            actual_marks=0,
            planned_marks=planned_total,
            issues=[BlueprintIssue("error", "The paper has no questions.")],
        )

    if planned_total and abs(actual_total - planned_total) > max(1.0, planned_total * 0.02):
        issues.append(
            BlueprintIssue(
                "error",
                f"Total is {actual_total:g} marks but the paper should be {planned_total:g}.",
            )
        )

    bloom_actual = _marks_by(questions, lambda q: q.bloom.value)
    bloom_planned = _marks_by_cell(blueprint, lambda c: c.bloom.value)
    issues += _compare(
        "Bloom level", bloom_actual, bloom_planned, actual_total, planned_total, tolerance
    )

    topic_actual = _marks_by(questions, lambda q: q.topic or "General")
    topic_planned = _marks_by_cell(blueprint, lambda c: c.topic)
    issues += _compare("Topic", topic_actual, topic_planned, actual_total, planned_total, tolerance)

    # Quality checks that have nothing to do with the blueprint but everything to do with
    # whether the paper is usable.
    issues += _quality_checks(paper)

    return BlueprintReport(
        matches=not any(i.severity == "error" for i in issues),
        actual_marks=actual_total,
        planned_marks=planned_total,
        issues=issues,
        bloom_actual=bloom_actual,
        bloom_planned=bloom_planned,
        topic_actual=topic_actual,
        topic_planned=topic_planned,
    )


def _marks_by(questions, key) -> dict[str, float]:  # noqa: ANN001
    out: dict[str, float] = {}
    for question in questions:
        out[key(question)] = out.get(key(question), 0) + question.marks
    return out


def _marks_by_cell(blueprint: Blueprint, key) -> dict[str, float]:  # noqa: ANN001
    out: dict[str, float] = {}
    for cell in blueprint.cells:
        out[key(cell)] = out.get(key(cell), 0) + cell.total_marks
    return out


def _compare(
    label: str,
    actual: dict[str, float],
    planned: dict[str, float],
    actual_total: float,
    planned_total: float,
    tolerance: float,
) -> list[BlueprintIssue]:
    if not planned or not planned_total or not actual_total:
        return []

    issues: list[BlueprintIssue] = []
    for key, planned_marks in planned.items():
        planned_share = planned_marks / planned_total
        actual_share = actual.get(key, 0) / actual_total
        drift = actual_share - planned_share

        if actual.get(key, 0) == 0:
            issues.append(BlueprintIssue("error", f"{label} '{key}' is not assessed at all."))
        elif abs(drift) > tolerance:
            direction = "over" if drift > 0 else "under"
            issues.append(
                BlueprintIssue(
                    "warning",
                    f"{label} '{key}' is {direction}-weighted: "
                    f"{actual_share:.0%} of marks against a planned {planned_share:.0%}.",
                )
            )

    for key in actual:
        if key not in planned:
            issues.append(BlueprintIssue("warning", f"{label} '{key}' was not in the blueprint."))
    return issues


def _quality_checks(paper: QuestionPaper) -> list[BlueprintIssue]:
    """Problems a teacher would spot immediately on reading the paper."""
    issues: list[BlueprintIssue] = []
    questions = paper.questions

    stems = [q.text.strip().lower() for q in questions]
    duplicates = {s for s in stems if stems.count(s) > 1}
    for stem in list(duplicates)[:3]:
        issues.append(BlueprintIssue("error", f"A question appears more than once: “{stem[:70]}…”"))

    if paper.meta.duration_minutes:
        estimated = paper.estimated_minutes
        if estimated > paper.meta.duration_minutes * 1.3:
            issues.append(
                BlueprintIssue(
                    "warning",
                    f"The paper looks like about {estimated:.0f} minutes of work but is "
                    f"timed at {paper.meta.duration_minutes}.",
                )
            )

    for question in questions:
        if question.type in (QuestionType.MCQ, QuestionType.MULTI_SELECT):
            issues += _mcq_checks(question)

    return issues


def _mcq_checks(question) -> list[BlueprintIssue]:  # noqa: ANN001
    """Classic multiple-choice flaws that let students score without knowing anything."""
    issues: list[BlueprintIssue] = []
    stem = question.text[:60]
    texts = [o.text.strip() for o in question.options]

    if len({t.lower() for t in texts}) != len(texts):
        issues.append(BlueprintIssue("error", f"Duplicate options in “{stem}…”"))

    lowered = [t.lower() for t in texts]
    if any(t.startswith(("all of the above", "none of the above")) for t in lowered):
        issues.append(
            BlueprintIssue(
                "warning",
                f"“{stem}…” uses an 'all/none of the above' option, which rewards "
                "partial knowledge with a full mark.",
            )
        )

    correct = [o for o in question.options if o.is_correct]
    if correct and len(texts) > 2:
        longest = max(texts, key=len)
        if correct[0].text.strip() == longest and len(longest) > 1.6 * (
            sum(len(t) for t in texts) / len(texts)
        ):
            issues.append(
                BlueprintIssue(
                    "warning",
                    f"In “{stem}…” the correct option is much longer than the distractors, "
                    "which gives it away.",
                )
            )

    return issues
