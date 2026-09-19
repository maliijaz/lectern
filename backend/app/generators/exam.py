"""Question paper generation.

The pipeline is: decide the topics → derive a blueprint → generate the questions for each
blueprint cell → check the paper against the blueprint → repair what is off → answer every
machine-markable question again independently and flag any key the second pass disputes →
assemble sections and variants.

Generating per blueprint cell rather than "write me a 50 mark paper" is what produces
coverage. Each call asks for a handful of questions on one topic, at one cognitive level,
in one format — a narrow enough task that an 8B model does it well.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.logging import get_logger
from app.generators import blueprint as blueprint_mod
from app.generators import source as source_mod
from app.generators import verify as verify_mod
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.common import Bloom, GenerationMeta
from app.schemas.paper import (
    Blueprint,
    BlueprintCell,
    PaperMeta,
    PaperSection,
    Question,
    QuestionPaper,
    QuestionType,
)
from app.schemas.requests import ExamRequest

log = get_logger(__name__)

EXAM_PERSONA = (
    "You are an experienced examiner. You write questions that distinguish a student who "
    "understands from one who has memorised. Your distractors are the specific mistakes "
    "students actually make, never filler. Your mark schemes say exactly what earns credit."
)

#: Questions per model call. A blueprint cell can legitimately want fifteen one-mark
#: objective questions, but asking for fifteen in one response gets four good ones and
#: eleven variations on the same idea — and on a local model it often runs out of tokens
#: mid-answer. Small batches, each told what has already been asked, hold quality up.
MAX_PER_CALL = 5

#: Human-readable section titles, and the order sections appear in a conventional paper.
SECTION_PLAN: list[tuple[str, tuple[QuestionType, ...], str]] = [
    (
        "Section A — Objective Questions",
        (
            QuestionType.MCQ,
            QuestionType.MULTI_SELECT,
            QuestionType.TRUE_FALSE,
            QuestionType.FILL_BLANK,
            QuestionType.MATCHING,
            QuestionType.ASSERTION_REASON,
        ),
        "Answer all questions. Choose the best option in each case.",
    ),
    (
        "Section B — Short Answer Questions",
        (QuestionType.SHORT_ANSWER, QuestionType.NUMERICAL, QuestionType.DIAGRAM_LABEL),
        "Answer all questions briefly. Show your working where relevant.",
    ),
    (
        "Section C — Long Answer Questions",
        (QuestionType.LONG_ANSWER, QuestionType.CASE_STUDY),
        "Answer in detail. Structure your response clearly.",
    ),
]


class QuestionBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    questions: list[Question] = Field(default_factory=list)


class TopicList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topics: list[str] = Field(
        default_factory=list, description="Assessable topics, most important first"
    )


async def generate(
    db: AsyncSession,
    provider: LLMProvider,
    settings: Settings,
    request: ExamRequest,
    *,
    progress=None,  # noqa: ANN001
) -> tuple[QuestionPaper, blueprint_mod.BlueprintReport, verify_mod.AnswerAudit]:
    """Produce a paper, the blueprint audit, and an independent check of its answers."""
    started = time.perf_counter()

    async def report(fraction: float, message: str) -> None:
        if progress is not None:
            await progress(fraction, message)

    await report(0.03, "Gathering source material")
    material = await source_mod.collect(
        db,
        settings=settings,
        topic=request.topic,
        document_ids=request.document_ids,
        queries=_source_queries(request),
        budget=20_000,
    )

    topics = request.topics
    if not topics:
        await report(0.1, "Identifying assessable topics")
        topics = await _find_topics(provider, settings, request, material)

    plan = request.blueprint or blueprint_mod.derive(
        total_marks=request.total_marks,
        duration_minutes=request.duration_minutes,
        topics=topics,
        question_types=request.question_types,
        bloom_mix=request.bloom_mix,
        difficulty_mix=request.difficulty_mix,
    )
    await report(
        0.15,
        f"Blueprint: {plan.planned_questions} questions, {plan.planned_marks:g} marks "
        f"across {len(plan.topics())} topics",
    )

    questions: list[Question] = []
    for index, cell in enumerate(plan.cells):
        cell_start = 0.15 + 0.6 * index / max(1, len(plan.cells))
        cell_end = 0.15 + 0.6 * (index + 1) / max(1, len(plan.cells))
        await report(
            cell_start,
            f"Writing {cell.count} × {cell.question_type.value} on “{cell.topic}” "
            f"({cell.bloom.value})",
        )

        # Bind the span as defaults: a closure over the loop variables would report
        # against whatever cell the loop had reached by the time it ran.
        async def cell_progress(
            fraction: float, message: str, lo: float = cell_start, hi: float = cell_end
        ) -> None:
            await report(lo + fraction * (hi - lo), message)

        questions.extend(
            await _write_cell(
                provider,
                settings,
                request,
                material,
                cell,
                existing=questions,
                progress=cell_progress,
            )
        )

    await report(0.8, "Assembling the paper")
    paper = _assemble(questions, request, plan)

    await report(0.85, "Checking the paper against the blueprint")
    check = blueprint_mod.validate(paper, plan)

    if not check.matches and check.errors:
        await report(0.88, "Repairing the paper")
        paper = await _repair(provider, settings, request, material, paper, plan, check)
        check = blueprint_mod.validate(paper, plan)

    audit = verify_mod.AnswerAudit()
    if request.verify_answers:
        # Check with different weights where the teacher has configured a second model.
        # Re-asking the same model is a weaker check than it looks — it confirms its own
        # mistakes — so a second opinion is only really independent when it is a second
        # model. Falls back to the generation model when none is set.
        from app.llm.registry import build_for_model

        checker = build_for_model(settings, settings.llm_verify_model)
        await report(
            0.92,
            "Checking the answer key"
            + (f" with {checker.model}" if checker.model != provider.model else ""),
        )
        audit = await verify_mod.verify_answers(
            checker, settings, paper, progress=_verification_progress(report)
        )
        audit.verifier_model = checker.model
        verify_mod.annotate(paper, audit)

    paper.generation = GenerationMeta(
        model=provider.model,
        provider=provider.name,
        seconds=round(time.perf_counter() - started, 2),
        grounded=material.grounded,
        source_documents=material.document_titles,
    )

    await report(
        1.0,
        f"{len(paper.questions)} questions, {paper.total_marks:g} marks. {check.summary()} "
        f"{audit.summary()}",
    )
    return paper, check, audit


def _verification_progress(report):  # noqa: ANN001, ANN201
    """Map the verifier's own 0..1 progress into the tail of the overall bar."""

    async def scaled(fraction: float, message: str) -> None:
        await report(0.92 + 0.07 * fraction, message)

    return scaled


def _source_queries(request: ExamRequest) -> list[str]:
    topic = request.topic or request.audience.subject or "the material"
    queries = [
        topic,
        f"key facts and definitions in {topic}",
        f"problems and calculations in {topic}",
        f"applications and case examples of {topic}",
    ]
    return queries + [f"{t} key points" for t in request.topics[:6]]


async def _find_topics(
    provider: LLMProvider,
    settings: Settings,
    request: ExamRequest,
    material: source_mod.SourceMaterial,
) -> list[str]:
    """Ask what is actually assessable, rather than assuming the teacher listed it."""
    prompt = f"""\
{f"SUBJECT: {request.topic}" if request.topic else ""}
{source_mod.audience_block(request.audience)}
{material.prompt_block()}

List the 3 to 8 distinct topics this material can fairly examine. Each should be a
teachable unit, specific enough to write questions about — "Photosynthesis: light
reactions", not "Biology". Order them by how much of the material they account for.
"""
    result = await generate_structured(
        provider,
        TopicList,
        [system(EXAM_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )
    topics = [t.strip() for t in result.topics if t.strip()][:8]
    return topics or [request.topic or "General"]


async def _write_cell(
    provider: LLMProvider,
    settings: Settings,
    request: ExamRequest,
    material: source_mod.SourceMaterial,
    cell: BlueprintCell,
    *,
    existing: list[Question],
    progress=None,  # noqa: ANN001 — optional async (fraction, message) callback
) -> list[Question]:
    """Generate the questions for one blueprint cell, in batches.

    Each batch is told what the previous ones produced, so the model varies what it asks
    rather than rephrasing its first idea.
    """
    produced: list[Question] = []
    remaining = cell.count
    batch_index = 0

    while remaining > 0:
        size = min(MAX_PER_CALL, remaining)
        if progress is not None and cell.count > MAX_PER_CALL:
            done = cell.count - remaining
            await progress(
                done / cell.count,
                f"{cell.topic} — {cell.question_type.value} {done + 1}–{done + size} "
                f"of {cell.count}",
            )
        produced.extend(
            await _write_batch(
                provider,
                settings,
                request,
                material,
                cell,
                size,
                existing=existing + produced,
            )
        )
        remaining -= size
        batch_index += 1
        # Stop early if the model has run dry rather than looping on empty responses.
        if len(produced) < batch_index * size * 0.5:
            log.warning(
                "Cell %s/%s is under-producing (%d of %d); stopping early",
                cell.topic,
                cell.question_type.value,
                len(produced),
                cell.count,
            )
            break

    return produced[: cell.count]


async def _write_batch(
    provider: LLMProvider,
    settings: Settings,
    request: ExamRequest,
    material: source_mod.SourceMaterial,
    cell: BlueprintCell,
    count: int,
    *,
    existing: list[Question],
) -> list[Question]:
    """One model call for up to :data:`MAX_PER_CALL` questions."""
    already = [q.text for q in existing if q.topic == cell.topic][-10:]
    avoid = (
        "ALREADY ASKED on this topic — do not ask these again or rephrase them:\n"
        + "\n".join(f"- {t}" for t in already)
        if already
        else ""
    )

    prompt = f"""\
Write exactly {count} {cell.question_type.value} question(s).

TOPIC: {cell.topic}
COGNITIVE LEVEL: {cell.bloom.value} — the question must require the student to
{_bloom_demand(cell.bloom)}
DIFFICULTY: {cell.difficulty.value}
MARKS EACH: {cell.marks_each:g}

{source_mod.audience_block(request.audience)}
{material.prompt_block()}
{f"TEACHER'S INSTRUCTIONS: {request.instructions}" if request.instructions else ""}

{avoid}

{_type_rules(cell.question_type, request)}

Set `type` to "{cell.question_type.value}", `topic` to "{cell.topic}", `bloom` to
"{cell.bloom.value}", `difficulty` to "{cell.difficulty.value}" and `marks` to
{cell.marks_each:g} on every question.
Estimate `estimated_minutes` from how long a prepared student would actually take.
{material.citation_rule()}

Verbs appropriate to this level: {", ".join(_verbs(cell.bloom))}
"""

    batch = await generate_structured(
        provider,
        QuestionBatch,
        [system(EXAM_PERSONA), user(prompt)],
        max_attempts=settings.llm_max_repair_attempts,
    )

    seen = {q.text.strip().lower() for q in existing}
    questions: list[Question] = []
    for question in batch.questions[:count]:
        if question.text.strip().lower() in seen:
            continue  # the model repeated itself despite being told not to
        seen.add(question.text.strip().lower())
        # The model sets these inconsistently; the blueprint is the source of truth.
        question.topic = question.topic or cell.topic
        question.bloom = cell.bloom
        question.difficulty = cell.difficulty
        question.marks = cell.marks_each
        if material.citations and question.citations:
            question.citations = (
                source_mod.resolve_citations(
                    [c.chunk_id or c.document_id for c in question.citations], material.citations
                )
                or question.citations
            )
        if not request.include_explanations:
            question.explanation = ""
        questions.append(question)

    if len(questions) < count:
        log.warning(
            "Batch for %s/%s produced %d of %d questions",
            cell.topic,
            cell.question_type.value,
            len(questions),
            count,
        )
    return questions


def _bloom_demand(bloom: Bloom) -> str:
    return {
        Bloom.REMEMBER: "recall a specific fact, term or step — not to reason about it",
        Bloom.UNDERSTAND: "explain an idea in their own words, or interpret something given",
        Bloom.APPLY: "use a rule, method or concept on a situation they have not seen before",
        Bloom.ANALYZE: "break something down, compare parts, or work out why a result follows",
        Bloom.EVALUATE: "make a judgement and defend it against an alternative",
        Bloom.CREATE: "design or construct something new that meets stated constraints",
    }[bloom]


def _verbs(bloom: Bloom) -> tuple[str, ...]:
    from app.schemas.common import BLOOM_VERBS

    return BLOOM_VERBS[bloom]


def _type_rules(qtype: QuestionType, request: ExamRequest) -> str:
    """Format-specific instructions. This is where question quality is won or lost."""
    negative = (
        f"\nNote: {request.negative_marking:g} marks are deducted for a wrong answer, so "
        "every distractor must be clearly wrong on inspection by a student who knows the "
        "material."
        if request.negative_marking
        else ""
    )

    match qtype:
        case QuestionType.MCQ:
            return (
                "Give exactly 4 options. Exactly one has is_correct=true.\n"
                "Every distractor must be a mistake a real student would make — a common "
                "confusion, an off-by-one, a plausible-but-wrong definition. Never filler, "
                "never obviously silly.\n"
                "All four options must be similar in length and grammatical form; a longer "
                "or more detailed correct answer gives the question away.\n"
                "Do not use 'all of the above' or 'none of the above'.\n"
                "In each option's `rationale`, name the specific misconception it captures.\n"
                "Put the full reasoning in `explanation`." + negative
            )
        case QuestionType.MULTI_SELECT:
            return (
                "Give 5 options with 2 or 3 correct. State in the stem how many to select.\n"
                "Each distractor must be a genuine near-miss." + negative
            )
        case QuestionType.TRUE_FALSE:
            return (
                "Set correct_bool. The statement must be unambiguously true or false — no "
                "'usually' or 'often'. Avoid negation in the stem.\n"
                "Explain in `explanation` why, and what makes the opposite tempting."
            )
        case QuestionType.FILL_BLANK:
            return (
                "Use ____ in the stem for each gap and list the accepted answers in `blanks` "
                "in order. Leave only terms that have one right answer; do not blank out a "
                "word that several correct answers would fit."
            )
        case QuestionType.MATCHING:
            return (
                "Give 4 to 6 pairs. The two columns must contain items of the same kind, so "
                "students cannot match by category alone."
            )
        case QuestionType.ASSERTION_REASON:
            return (
                "Write an Assertion and a Reason in the stem, then give exactly these four "
                "options: both true and the reason explains the assertion; both true but the "
                "reason does not explain it; assertion true, reason false; assertion false, "
                "reason true. Mark the correct one."
            )
        case QuestionType.NUMERICAL:
            return (
                "Set numeric_answer, unit and a sensible tolerance. Put the full solution in "
                "`working`, one step per line, with units carried through.\n"
                "Use realistic values — an answer of exactly 100 looks contrived."
            )
        case QuestionType.SHORT_ANSWER:
            return (
                "The answer should take a student two to four sentences.\n"
                "Fill `answer` with a model response, `keywords` with the terms that must "
                "appear, and `rubric_points` with what earns each mark."
            )
        case QuestionType.LONG_ANSWER:
            return (
                "This is a structured or essay answer. The stem must make the expected scope "
                "clear (how many factors, whether to evaluate or only describe).\n"
                "Fill `rubric_points` with one entry per creditable element and its marks; "
                "they must sum to the question's marks. Fill `answer` with a model response."
            )
        case QuestionType.CASE_STUDY:
            return (
                "Write a `scenario` of 80–150 words with concrete, specific details — names, "
                "numbers, a situation. Then 3 or 4 `sub_questions` that escalate from "
                "comprehension to judgement, each with its own marks and model answer. The "
                "sub-question marks must sum to the question's marks."
            )
        case QuestionType.DIAGRAM_LABEL:
            return (
                "Describe the diagram in `figure` with kind='diagram' and Mermaid source in "
                "`figure.spec`, and list the correct labels in `labels`."
            )
        case _:
            return "Fill the fields appropriate to this question type."


def _assemble(questions: list[Question], request: ExamRequest, plan: Blueprint) -> QuestionPaper:
    """Group questions into conventional exam sections."""
    sections: list[PaperSection] = []
    remaining = list(questions)

    for title, types, instructions in SECTION_PLAN:
        chosen = [q for q in remaining if q.type in types]
        if not chosen:
            continue
        remaining = [q for q in remaining if q not in chosen]
        # Within a section, easy questions first: students settle before the hard ones.
        chosen.sort(
            key=lambda q: (q.marks, {"easy": 0, "medium": 1, "hard": 2}[q.difficulty.value])
        )
        sections.append(PaperSection(title=title, instructions=instructions, questions=chosen))

    if remaining:
        sections.append(PaperSection(title="Section D — Further Questions", questions=remaining))

    total = sum(s.total_marks for s in sections)
    instructions = list(request.instructions_list) or _default_instructions(request, total)

    return QuestionPaper(
        meta=PaperMeta(
            institution=request.institution,
            course=request.audience.subject or request.topic,
            exam_name=request.exam_name or request.title or "Examination",
            grade_level=request.audience.grade_level,
            duration_minutes=request.duration_minutes,
            total_marks=total,
            date=request.date,
            instructions=instructions,
        ),
        blueprint=plan,
        sections=sections,
    )


def _default_instructions(request: ExamRequest, total: float) -> list[str]:
    lines = [
        "Answer all questions unless a section says otherwise.",
        f"The paper carries {total:g} marks in total.",
        f"Time allowed: {request.duration_minutes} minutes.",
        "Marks for each question are shown in brackets.",
        "Write clearly. Show your working where it is asked for.",
    ]
    if request.negative_marking:
        lines.insert(
            1,
            f"{request.negative_marking:g} mark(s) will be deducted for each incorrect "
            "answer in the objective section.",
        )
    return lines


async def _repair(
    provider: LLMProvider,
    settings: Settings,
    request: ExamRequest,
    material: source_mod.SourceMaterial,
    paper: QuestionPaper,
    plan: Blueprint,
    report: blueprint_mod.BlueprintReport,
) -> QuestionPaper:
    """Fix blueprint gaps by generating only what is missing.

    Regenerating the whole paper would be slower and would throw away good questions; the
    gaps are usually one under-assessed topic.
    """
    missing_topics = [
        topic
        for topic, planned in report.topic_planned.items()
        if report.topic_actual.get(topic, 0) == 0
    ]

    for topic in missing_topics[:3]:
        cell = next(
            (c for c in plan.cells if c.topic == topic),
            BlueprintCell(topic=topic, count=1, marks_each=2),
        )
        log.info("Repairing blueprint gap: topic %r", topic)
        extra = await _write_cell(
            provider, settings, request, material, cell, existing=paper.questions
        )
        if extra:
            _place(paper, extra)

    # Drop exact duplicates, keeping the first occurrence.
    seen: set[str] = set()
    for section in paper.sections:
        kept = []
        for question in section.questions:
            key = question.text.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(question)
        section.questions = kept

    paper.meta.total_marks = paper.total_marks
    return paper


def _place(paper: QuestionPaper, questions: list[Question]) -> None:
    """Insert questions into the section that matches their type."""
    for question in questions:
        target = None
        for title, types, _ in SECTION_PLAN:
            if question.type in types:
                target = next((s for s in paper.sections if s.title == title), None)
                if target is None:
                    target = PaperSection(title=title)
                    paper.sections.append(target)
                break
        (target or paper.sections[-1]).questions.append(question)


def build_variants(paper: QuestionPaper, count: int) -> list[QuestionPaper]:
    """Set A, B, C … — same questions, deterministically reshuffled."""
    if count <= 1:
        return [paper]
    variants = []
    for index in range(count):
        label = f"Set {chr(ord('A') + index)}"
        if index == 0:
            first = paper.model_copy(deep=True)
            first.meta = first.meta.model_copy(update={"variant_label": label})
            variants.append(first)
        else:
            variants.append(paper.variant(label, seed=1000 + index))
    return variants
