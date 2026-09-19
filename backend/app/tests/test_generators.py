"""Generator pipelines, driven by a scripted fake model.

These tests exist to check the *orchestration*: that the two-pass flows call the model the
right number of times, that blueprint coverage is enforced, that structural slides are
inserted, and that nothing downstream depends on the model behaving well. Question quality
is the model's job; correct assembly is ours.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from app.config import get_settings
from app.generators import blueprint as blueprint_mod
from app.llm.fake import FakeProvider
from app.schemas.common import Audience, Bloom, Depth, Difficulty
from app.schemas.deck import SlideLayout
from app.schemas.paper import QuestionType
from app.schemas.requests import ExamRequest, NotesRequest, SlidesRequest

# --------------------------------------------------------------------------- responders


def _outline_responder(count: int):
    def build(_schema: dict[str, Any], _messages: list) -> dict[str, Any]:
        return {
            "title": "Photosynthesis",
            "subtitle": "Biology — Grade 10",
            "objectives": [
                {"text": "Explain how light energy becomes chemical energy", "bloom": "understand"},
                {"text": "Compare the light and dark reactions", "bloom": "analyze"},
            ],
            "slides": [
                {
                    "heading": f"Concept {i}",
                    "layout": "bullets",
                    "intent": f"Teach concept {i}",
                    "key_points": [f"point {i}a", f"point {i}b"],
                    "duration_minutes": 2.0,
                }
                for i in range(count)
            ],
        }

    return build


def _slide_batch_responder(_schema: dict[str, Any], messages: list) -> dict[str, Any]:
    """Return as many slides as the prompt asked for."""
    prompt = messages[-1].content
    wanted = prompt.count("  heading:") or 1
    return {
        "slides": [
            {
                "layout": "bullets",
                "heading": f"Written slide {i}",
                "bullets": ["short point", "another point"],
                "speaker_notes": "Explain the point, then ask the class for an example.",
                "duration_minutes": 2.0,
            }
            for i in range(wanted)
        ]
    }


def _notes_outline_responder(_schema: dict[str, Any], _messages: list) -> dict[str, Any]:
    return {
        "title": "Photosynthesis",
        "overview": "Plants turn light into sugar.",
        "objectives": [{"text": "Describe the Calvin cycle", "bloom": "understand"}],
        "section_plan": [
            {
                "heading": "Light reactions",
                "covers": "How photons drive electron transport",
                "key_points": ["thylakoid", "photolysis"],
                "needs_example": False,
            },
            {
                "heading": "The Calvin cycle",
                "covers": "How carbon is fixed",
                "key_points": ["rubisco", "stroma"],
                "needs_example": True,
            },
        ],
    }


def _note_section_responder(_schema: dict[str, Any], messages: list) -> dict[str, Any]:
    prompt = messages[-1].content
    heading = "Light reactions" if "Light reactions" in prompt else "The Calvin cycle"
    return {
        "heading": heading,
        "body": "A clear explanation of the mechanism, in plain language. " * 12,
        "key_terms": [{"term": "Rubisco", "definition": "The enzyme that fixes CO2"}],
        "callouts": [
            {
                "kind": "misconception",
                "title": "Not 'the dark reaction'",
                "body": "The Calvin cycle does not require darkness.",
            }
        ],
    }


def _notes_closing_responder(_schema: dict[str, Any], _messages: list) -> dict[str, Any]:
    return {
        "title": "Photosynthesis",
        "summary": "Light reactions capture energy; the Calvin cycle spends it on carbon.",
        "review_questions": [
            {"question": "Where does photolysis occur?", "answer": "The thylakoid membrane."}
        ],
        "further_reading": ["Any introductory plant physiology text"],
        "glossary": [{"term": "Stroma", "definition": "The fluid around the thylakoids"}],
        "sections": [],
    }


def _topic_responder(_schema: dict[str, Any], _messages: list) -> dict[str, Any]:
    return {"topics": ["Light reactions", "Calvin cycle"]}


#: Makes every stub question stem unique. A real model repeating itself across blueprint
#: cells is exactly what the paper's duplicate check is meant to catch, so the stub must
#: not trip it accidentally.
_stem_counter = itertools.count()


def _question_batch_responder(_schema: dict[str, Any], messages: list) -> dict[str, Any]:
    """Build valid questions of whatever type the prompt asked for."""
    prompt = messages[-1].content
    qtype = next(
        (t.value for t in QuestionType if f'Set `type` to "{t.value}"' in prompt),
        "mcq",
    )
    count = 1
    for line in prompt.splitlines():
        if line.startswith("Write exactly "):
            count = int(line.split()[2])
            break
    topic = prompt.split("TOPIC: ", 1)[1].split("\n", 1)[0] if "TOPIC: " in prompt else "General"

    return {"questions": [_make_question(qtype, topic, next(_stem_counter)) for _ in range(count)]}


def _make_question(qtype: str, topic: str, index: int) -> dict[str, Any]:
    base = {"type": qtype, "text": f"{topic} question {index}", "topic": topic, "marks": 1}
    match qtype:
        case "mcq" | "assertion_reason":
            base["options"] = [
                {"text": "Correct answer", "is_correct": True},
                {"text": "Plausible distractor one"},
                {"text": "Plausible distractor two"},
                {"text": "Plausible distractor three"},
            ]
        case "multi_select":
            base["options"] = [
                {"text": "Right one", "is_correct": True},
                {"text": "Right two", "is_correct": True},
                {"text": "Wrong one"},
            ]
        case "true_false":
            base["correct_bool"] = True
        case "fill_blank":
            base["text"] = f"{topic} uses ____ to fix carbon."
            base["blanks"] = ["rubisco"]
        case "matching":
            base["pairs"] = [
                {"left": "Stroma", "right": "Calvin cycle"},
                {"left": "Thylakoid", "right": "Light reactions"},
            ]
        case "numerical":
            base["numeric_answer"] = 42.0
            base["unit"] = "kJ"
            base["working"] = "Step one. Step two."
        case "case_study":
            base["scenario"] = "A greenhouse raises CO2 concentration by 50%."
            base["sub_questions"] = [
                {"label": "a", "text": "What happens to the rate?", "marks": 2, "answer": "Rises"},
                {"label": "b", "text": "Why?", "marks": 3, "answer": "Rubisco saturation"},
            ]
        case "diagram_label":
            base["labels"] = ["Thylakoid", "Stroma"]
            base["figure"] = {
                "kind": "diagram",
                "spec": "graph TD; A-->B",
                "alt_text": "Chloroplast",
            }
        case _:
            base["answer"] = "A model answer of two or three sentences."
            base["rubric_points"] = [{"description": "States the mechanism", "marks": 1}]
    return base


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        responders={
            "DeckOutline": _outline_responder(10),
            "SlideBatch": _slide_batch_responder,
            "NotesOutline": _notes_outline_responder,
            "NoteSection": _note_section_responder,
            "LectureNotes": _notes_closing_responder,
            "TopicList": _topic_responder,
            "QuestionBatch": _question_batch_responder,
        }
    )


# --------------------------------------------------------------------------- slides


async def test_slides_generation_produces_a_full_deck(db, provider) -> None:
    from app.generators import slides

    deck = await slides.generate(
        db,
        provider,
        get_settings(),
        SlidesRequest(topic="Photosynthesis", slide_count=10, audience=Audience(grade_level="10")),
    )

    assert deck.title == "Photosynthesis"
    assert len(deck.slides) >= 10
    assert deck.slides[0].layout == SlideLayout.TITLE
    assert deck.slides[-1].layout == SlideLayout.SUMMARY
    assert any("able to do" in s.heading for s in deck.slides[:3])
    assert all(s.speaker_notes or s.layout == SlideLayout.TITLE for s in deck.slides)
    assert deck.duration_minutes > 0


async def test_slide_count_derived_from_lecture_length() -> None:
    request = SlidesRequest(topic="X", lecture_minutes=50)
    assert request.slide_count == 25


async def test_slides_batching_calls_the_model_once_per_batch(db, provider) -> None:
    from app.generators import slides

    await slides.generate(
        db, provider, get_settings(), SlidesRequest(topic="Photosynthesis", slide_count=10)
    )
    # 1 outline pass + ceil(10/4) = 3 content passes.
    assert len(provider.calls) == 4


async def test_progress_is_reported(db, provider) -> None:
    from app.generators import slides

    seen: list[tuple[float, str]] = []

    async def record(fraction: float, message: str) -> None:
        seen.append((fraction, message))

    await slides.generate(
        db,
        provider,
        get_settings(),
        SlidesRequest(topic="X", slide_count=6),
        progress=record,
    )
    assert seen[0][0] < seen[-1][0]
    assert seen[-1][0] == 1.0


# --------------------------------------------------------------------------- notes


async def test_notes_generation_fills_every_part(db, provider) -> None:
    from app.generators import notes

    result = await notes.generate(
        db,
        provider,
        get_settings(),
        NotesRequest(topic="Photosynthesis", depth=Depth.STANDARD),
    )

    assert result.title == "Photosynthesis"
    assert len(result.sections) == 2
    assert result.summary
    assert result.review_questions
    assert result.glossary
    # The glossary merges per-section terms with the closing pass.
    terms = {t.term for t in result.glossary}
    assert {"Rubisco", "Stroma"} <= terms
    assert result.word_count() > 100


async def test_notes_respects_max_sections(db, provider) -> None:
    from app.generators import notes

    result = await notes.generate(
        db, provider, get_settings(), NotesRequest(topic="X", max_sections=1)
    )
    assert len(result.sections) == 1


async def test_notes_can_omit_optional_parts(db, provider) -> None:
    from app.generators import notes

    result = await notes.generate(
        db,
        provider,
        get_settings(),
        NotesRequest(topic="X", include_review_questions=False, include_glossary=False),
    )
    assert result.review_questions == []
    assert result.glossary == []


# --------------------------------------------------------------------------- exams


async def test_exam_generation_matches_its_blueprint(db, provider) -> None:
    from app.generators import exam

    paper, report, _audit = await exam.generate(
        db,
        provider,
        get_settings(),
        ExamRequest(
            topic="Photosynthesis",
            total_marks=40,
            duration_minutes=60,
            question_types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
        ),
    )

    assert paper.questions
    assert paper.sections
    assert paper.blueprint is not None
    # Every topic in the blueprint is actually assessed.
    assert not [e for e in report.errors if "not assessed at all" in e]
    for question in paper.questions:
        assert question.topic
        assert question.marks > 0


async def test_exam_sections_are_ordered_conventionally(db, provider) -> None:
    from app.generators import exam

    paper, _report, _audit = await exam.generate(
        db,
        provider,
        get_settings(),
        ExamRequest(
            topic="X", total_marks=40, question_types=list(QuestionType), verify_answers=False
        ),
    )
    titles = [s.title for s in paper.sections]
    assert titles == sorted(titles, key=lambda t: ["A", "B", "C", "D"].index(t.split(" — ")[0][-1]))


async def test_exam_variants_are_distinct_but_equivalent(db, provider) -> None:
    from app.generators import exam

    paper, _report, _audit = await exam.generate(
        db,
        provider,
        get_settings(),
        ExamRequest(topic="X", total_marks=30, variants=3, verify_answers=False),
    )
    variants = exam.build_variants(paper, 3)

    assert [v.meta.variant_label for v in variants] == ["Set A", "Set B", "Set C"]
    assert len({v.total_marks for v in variants}) == 1
    assert {q.text for q in variants[0].questions} == {q.text for q in variants[2].questions}


# --------------------------------------------------------------------------- blueprint


def test_derive_hits_the_exact_mark_total() -> None:
    for total in (20, 25, 35, 50, 75, 100):
        plan = blueprint_mod.derive(
            total_marks=total,
            duration_minutes=90,
            topics=["A", "B", "C"],
            question_types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
        )
        assert plan.planned_marks == pytest.approx(total, abs=0.02), f"total={total}"


def test_derive_covers_every_topic() -> None:
    plan = blueprint_mod.derive(
        total_marks=60,
        duration_minutes=90,
        topics=["Cells", "Genetics", "Ecology", "Evolution"],
        question_types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
    )
    assert set(plan.topics()) == {"Cells", "Genetics", "Ecology", "Evolution"}


def test_derive_respects_a_custom_bloom_mix() -> None:
    plan = blueprint_mod.derive(
        total_marks=100,
        duration_minutes=120,
        topics=["A"],
        question_types=[QuestionType.MCQ, QuestionType.LONG_ANSWER],
        bloom_mix={Bloom.REMEMBER: 0.5, Bloom.ANALYZE: 0.5},
    )
    levels = {c.bloom for c in plan.cells}
    assert levels == {Bloom.REMEMBER, Bloom.ANALYZE}


def test_derive_picks_suitable_formats_per_level() -> None:
    plan = blueprint_mod.derive(
        total_marks=50,
        duration_minutes=60,
        topics=["A"],
        question_types=[QuestionType.MCQ, QuestionType.LONG_ANSWER],
        bloom_mix={Bloom.REMEMBER: 0.5, Bloom.EVALUATE: 0.5},
    )
    by_bloom = {c.bloom: c.question_type for c in plan.cells}
    assert by_bloom[Bloom.REMEMBER] == QuestionType.MCQ
    # You cannot assess `evaluate` with a multiple-choice item.
    assert by_bloom[Bloom.EVALUATE] == QuestionType.LONG_ANSWER


def test_validate_flags_a_missing_topic() -> None:
    from app.schemas.paper import Option, PaperSection, Question, QuestionPaper

    plan = blueprint_mod.derive(
        total_marks=10,
        duration_minutes=30,
        topics=["Covered", "Ignored"],
        question_types=[QuestionType.MCQ],
    )
    paper = QuestionPaper(
        sections=[
            PaperSection(
                title="A",
                questions=[
                    Question(
                        type=QuestionType.MCQ,
                        text=f"Q{i}",
                        topic="Covered",
                        marks=1,
                        options=[Option(text="a", is_correct=True), Option(text="b")],
                    )
                    for i in range(10)
                ],
            )
        ]
    )
    report = blueprint_mod.validate(paper, plan)
    assert not report.matches
    assert any("'Ignored' is not assessed" in e for e in report.errors)


def test_validate_flags_duplicate_questions() -> None:
    from app.schemas.paper import Option, PaperSection, Question, QuestionPaper

    duplicate = Question(
        type=QuestionType.MCQ,
        text="What is a cell?",
        topic="A",
        options=[Option(text="a", is_correct=True), Option(text="b")],
    )
    paper = QuestionPaper(
        sections=[PaperSection(title="A", questions=[duplicate, duplicate.model_copy(deep=True)])]
    )
    report = blueprint_mod.validate(
        paper,
        blueprint_mod.derive(
            total_marks=2, duration_minutes=10, topics=["A"], question_types=[QuestionType.MCQ]
        ),
    )
    assert any("appears more than once" in e for e in report.errors)


def test_validate_warns_about_all_of_the_above() -> None:
    from app.schemas.paper import Option, PaperSection, Question, QuestionPaper

    question = Question(
        type=QuestionType.MCQ,
        text="Which are organelles?",
        topic="A",
        options=[
            Option(text="Nucleus"),
            Option(text="Ribosome"),
            Option(text="All of the above", is_correct=True),
        ],
    )
    paper = QuestionPaper(sections=[PaperSection(title="A", questions=[question])])
    report = blueprint_mod.validate(
        paper,
        blueprint_mod.derive(
            total_marks=1, duration_minutes=5, topics=["A"], question_types=[QuestionType.MCQ]
        ),
    )
    assert any("all/none of the above" in w for w in report.warnings)


def test_validate_warns_when_the_paper_is_too_long_for_the_time() -> None:
    from app.schemas.paper import PaperMeta, PaperSection, Question, QuestionPaper

    questions = [
        Question(
            type=QuestionType.LONG_ANSWER,
            text=f"Discuss topic {i}",
            topic="A",
            marks=10,
            estimated_minutes=20,
            answer="x",
        )
        for i in range(5)
    ]
    paper = QuestionPaper(
        meta=PaperMeta(duration_minutes=60),
        sections=[PaperSection(title="A", questions=questions)],
    )
    report = blueprint_mod.validate(
        paper,
        blueprint_mod.derive(
            total_marks=50,
            duration_minutes=60,
            topics=["A"],
            question_types=[QuestionType.LONG_ANSWER],
        ),
    )
    assert any("minutes of work" in w for w in report.warnings)


def test_reconcile_spreads_extra_marks_rather_than_piling_them_on_one_cell() -> None:
    """A single topic must not end up with a wall of one-mark questions.

    Topping up whichever cell best fits the shortfall is arithmetically correct and
    produces an unteachable paper — this guards the fix.
    """
    plan = blueprint_mod.derive(
        total_marks=30,
        duration_minutes=45,
        topics=["Light reactions"],
        question_types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
    )
    assert plan.planned_marks == pytest.approx(30, abs=0.02)
    biggest = max(c.count for c in plan.cells)
    assert biggest <= 10, f"one cell wants {biggest} questions: {plan.cells}"


def test_no_blueprint_cell_demands_an_unreasonable_batch() -> None:
    for total in (20, 30, 50, 80, 120):
        plan = blueprint_mod.derive(
            total_marks=total,
            duration_minutes=120,
            topics=["A", "B"],
            question_types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER, QuestionType.LONG_ANSWER],
        )
        assert plan.planned_marks == pytest.approx(total, abs=0.02)
        assert all(c.count <= 20 for c in plan.cells), f"total={total}: {plan.cells}"


async def test_large_cells_are_generated_in_batches(provider) -> None:
    """A cell wanting many questions must become several small model calls."""
    from app.generators import exam
    from app.generators.source import SourceMaterial
    from app.schemas.paper import BlueprintCell

    cell = BlueprintCell(topic="Cells", count=12, marks_each=1, question_type=QuestionType.MCQ)
    questions = await exam._write_cell(
        provider,
        get_settings(),
        ExamRequest(topic="X", total_marks=12),
        SourceMaterial(),
        cell,
        existing=[],
    )

    assert len(questions) == 12
    # 12 questions at a cap of 5 per call means three calls, not one.
    assert len(provider.calls) == 3
    # Every stem is distinct — each batch was told what the previous ones produced.
    assert len({q.text for q in questions}) == 12


def test_difficulty_skews_harder_at_higher_bloom_levels() -> None:
    plan = blueprint_mod.derive(
        total_marks=100,
        duration_minutes=120,
        topics=["A"],
        question_types=[QuestionType.MCQ, QuestionType.LONG_ANSWER],
        bloom_mix={Bloom.REMEMBER: 0.5, Bloom.EVALUATE: 0.5},
        difficulty_mix={Difficulty.EASY: 0.4, Difficulty.MEDIUM: 0.3, Difficulty.HARD: 0.3},
    )
    by_bloom = {c.bloom: c.difficulty for c in plan.cells}
    assert by_bloom[Bloom.REMEMBER] == Difficulty.EASY
    assert by_bloom[Bloom.EVALUATE] == Difficulty.HARD
