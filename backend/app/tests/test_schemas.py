"""Schema behaviour: repairs, validation rules, and the derived values renderers rely on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ARTIFACT_SCHEMAS, Bloom, Difficulty, Option, Question, QuestionType
from app.schemas.deck import Slide, SlideLayout
from app.schemas.paper import (
    Blueprint,
    BlueprintCell,
    MatchPair,
    PaperSection,
    QuestionPaper,
    SubQuestion,
)
from app.schemas.rubric import Criterion, Rubric

# --------------------------------------------------------------------------- questions


def test_mcq_requires_a_correct_option() -> None:
    with pytest.raises(ValidationError, match="no option marked is_correct"):
        Question(
            type=QuestionType.MCQ,
            text="Which is a noble gas?",
            options=[Option(text="Neon"), Option(text="Oxygen")],
        )


def test_mcq_rejects_two_correct_options() -> None:
    with pytest.raises(ValidationError, match="exactly one correct option"):
        Question(
            type=QuestionType.MCQ,
            text="Pick one",
            options=[Option(text="A", is_correct=True), Option(text="B", is_correct=True)],
        )


def test_multi_select_allows_several_correct_options() -> None:
    question = Question(
        type=QuestionType.MULTI_SELECT,
        text="Select all prime numbers",
        options=[
            Option(text="2", is_correct=True),
            Option(text="3", is_correct=True),
            Option(text="4"),
        ],
    )
    assert question.answer_text() == "2; 3"


def test_true_false_recovered_from_option_list() -> None:
    """Models often answer a true/false as a two-option MCQ; that should be repaired."""
    question = Question(
        type=QuestionType.TRUE_FALSE,
        text="Water boils at 100 C at sea level.",
        options=[Option(text="True", is_correct=True), Option(text="False")],
    )
    assert question.correct_bool is True
    assert question.options == []
    assert question.answer_text() == "True"


def test_fill_blank_gets_a_visible_gap() -> None:
    question = Question(
        type=QuestionType.FILL_BLANK,
        text="The powerhouse of the cell is the mitochondrion.",
        blanks=["mitochondrion"],
    )
    assert "____" in question.text


def test_numerical_gets_a_default_tolerance() -> None:
    question = Question(
        type=QuestionType.NUMERICAL, text="Compute 10/3", numeric_answer=3.333, unit="m"
    )
    assert question.tolerance == pytest.approx(0.03333)
    assert question.answer_text() == "3.333 m"


def test_case_study_marks_come_from_its_parts() -> None:
    question = Question(
        type=QuestionType.CASE_STUDY,
        text="Read the scenario and answer.",
        marks=99,
        scenario="A factory doubles output...",
        sub_questions=[
            SubQuestion(label="a", text="What changed?", marks=2, answer="Output doubled"),
            SubQuestion(label="b", text="Why?", marks=3, answer="New machinery"),
        ],
    )
    assert question.marks == 5


def test_long_answer_derives_a_mark_scheme_from_its_answer() -> None:
    question = Question(
        type=QuestionType.LONG_ANSWER,
        text="Discuss the causes of the war.",
        marks=10,
        answer="Economic, political and territorial factors combined...",
    )
    assert len(question.rubric_points) == 1
    assert question.rubric_points[0].marks == 10


def test_written_question_without_answer_or_scheme_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs an answer or a mark scheme"):
        Question(type=QuestionType.SHORT_ANSWER, text="Explain osmosis.")


def test_auto_gradable_flag() -> None:
    mcq = Question(
        type=QuestionType.MCQ,
        text="q",
        options=[Option(text="a", is_correct=True), Option(text="b")],
    )
    essay = Question(type=QuestionType.LONG_ANSWER, text="q", answer="a")
    assert mcq.auto_gradable
    assert not essay.auto_gradable


# --------------------------------------------------------------------------- variants


def _sample_paper() -> QuestionPaper:
    questions = [
        Question(
            type=QuestionType.MCQ,
            text=f"Question {i}",
            marks=2,
            options=[
                Option(text=f"correct-{i}", is_correct=True),
                Option(text=f"wrong-{i}-1"),
                Option(text=f"wrong-{i}-2"),
                Option(text=f"wrong-{i}-3"),
            ],
        )
        for i in range(8)
    ]
    return QuestionPaper(sections=[PaperSection(title="Section A", questions=questions)])


def test_variant_keeps_the_same_questions_and_answers() -> None:
    original = _sample_paper()
    variant = original.variant("Set B", seed=7)

    assert variant.meta.variant_label == "Set B"
    assert variant.total_marks == original.total_marks
    assert {q.text for q in variant.questions} == {q.text for q in original.questions}
    # Every question still has exactly one correct option after shuffling.
    for question in variant.questions:
        assert len(question.correct_options()) == 1


def test_variant_actually_reorders() -> None:
    original = _sample_paper()
    variant = original.variant("Set B", seed=7)
    assert [q.text for q in variant.questions] != [q.text for q in original.questions]


def test_variants_are_deterministic() -> None:
    paper = _sample_paper()
    assert [q.text for q in paper.variant("B", 3).questions] == [
        q.text for q in paper.variant("B", 3).questions
    ]


def test_matching_shuffle_preserves_the_pair_set() -> None:
    question = Question(
        type=QuestionType.MATCHING,
        text="Match the capitals",
        pairs=[
            MatchPair(left="France", right="Paris"),
            MatchPair(left="Japan", right="Tokyo"),
            MatchPair(left="Peru", right="Lima"),
        ],
    )
    paper = QuestionPaper(sections=[PaperSection(title="A", questions=[question])])
    shuffled = paper.variant("B", seed=1).questions[0]
    assert {p.right for p in shuffled.pairs} == {"Paris", "Tokyo", "Lima"}


def test_choose_n_section_counts_only_the_highest_marks() -> None:
    section = PaperSection(
        title="Section C",
        choose_count=2,
        questions=[
            Question(type=QuestionType.LONG_ANSWER, text="a", marks=10, answer="x"),
            Question(type=QuestionType.LONG_ANSWER, text="b", marks=10, answer="x"),
            Question(type=QuestionType.LONG_ANSWER, text="c", marks=10, answer="x"),
        ],
    )
    assert section.total_marks == 20


# --------------------------------------------------------------------------- blueprint


def test_blueprint_totals() -> None:
    blueprint = Blueprint(
        total_marks=50,
        cells=[
            BlueprintCell(topic="Cells", bloom=Bloom.REMEMBER, count=10, marks_each=1),
            BlueprintCell(
                topic="Genetics",
                bloom=Bloom.APPLY,
                difficulty=Difficulty.HARD,
                question_type=QuestionType.LONG_ANSWER,
                count=4,
                marks_each=10,
            ),
        ],
    )
    assert blueprint.planned_marks == 50
    assert blueprint.planned_questions == 14
    assert blueprint.topics() == ["Cells", "Genetics"]


# --------------------------------------------------------------------------- slides


def test_two_column_slide_is_repaired_from_bullets() -> None:
    slide = Slide(
        layout=SlideLayout.TWO_COLUMN,
        heading="Advantages and drawbacks",
        bullets=["Fast", "Cheap", "Fragile", "Noisy"],
    )
    assert len(slide.columns) == 2
    assert slide.columns[0].bullets == ["Fast", "Cheap"]
    assert slide.bullets == []


def test_diagram_slide_without_a_spec_falls_back_to_bullets() -> None:
    slide = Slide(layout=SlideLayout.DIAGRAM, heading="Water cycle", bullets=["Evaporation"])
    assert slide.layout == SlideLayout.BULLETS


# --------------------------------------------------------------------------- rubric


def test_rubric_fills_totals_and_equal_weights() -> None:
    rubric = Rubric(
        title="Essay rubric",
        criteria=[
            Criterion(name="Argument", max_points=4),
            Criterion(name="Evidence", max_points=4),
            Criterion(name="Clarity", max_points=2),
        ],
    )
    assert rubric.total_points == 10
    assert {c.weight_percent for c in rubric.criteria} == {33.33}


# --------------------------------------------------------------------------- all schemas


@pytest.mark.parametrize("kind", sorted(ARTIFACT_SCHEMAS))
def test_every_artifact_schema_emits_usable_json_schema(kind: str) -> None:
    """Each schema must serialise to JSON Schema — that is what constrains decoding."""
    schema = ARTIFACT_SCHEMAS[kind].model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
