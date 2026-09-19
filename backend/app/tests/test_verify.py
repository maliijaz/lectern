"""The independent answer-key check.

Written against a real failure. A generated paper asked "which is the primary site of the
light-dependent reactions?" and marked *Chloroplast stroma* correct over *Thylakoid
membrane* — with a rationale that contradicted the marking in the same breath. The paper
passed blueprint validation because it was structurally perfect. Only a second opinion on
the content catches that, and a wrong answer key is the most harmful thing this product
could hand a teacher.
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.generators import verify
from app.llm.fake import FakeProvider
from app.schemas.paper import (
    Option,
    PaperSection,
    Question,
    QuestionPaper,
    QuestionType,
)

SETTINGS = get_settings()


def _mcq(text: str, options: list[tuple[str, bool]], topic: str = "Photosynthesis") -> Question:
    return Question(
        type=QuestionType.MCQ,
        text=text,
        topic=topic,
        marks=1,
        options=[Option(text=t, is_correct=c) for t, c in options],
    )


def _paper(*questions: Question) -> QuestionPaper:
    return QuestionPaper(sections=[PaperSection(title="Section A", questions=list(questions))])


#: The question that started this, with the key it actually produced.
THE_BAD_QUESTION = _mcq(
    "Which of the following is the primary site of the light-dependent reactions?",
    [("Chloroplast stroma", True), ("Thylakoid membrane", False), ("Nucleus", False)],
)

A_GOOD_QUESTION = _mcq(
    "Which enzyme fixes carbon dioxide in the Calvin cycle?",
    [("Rubisco", True), ("Amylase", False), ("Catalase", False)],
)


def _verifier(answers: list[dict]) -> FakeProvider:
    """A provider that returns a fixed answer sheet."""
    return FakeProvider(scripted=[json.dumps({"answers": answers})])


# --------------------------------------------------------------------------- the point


async def test_a_wrong_answer_key_is_caught() -> None:
    """The exact failure from the live run."""
    paper = _paper(THE_BAD_QUESTION)
    # An independent pass says B (thylakoid), the key says A (stroma).
    provider = _verifier(
        [
            {
                "number": 1,
                "answer": "B",
                "confidence": 0.95,
                "reason": "The light-dependent reactions occur in the thylakoid membrane.",
            }
        ]
    )

    audit = await verify.verify_answers(provider, SETTINGS, paper)

    assert audit.checked == 1
    assert not audit.ok
    assert len(audit.disagreements) == 1

    found = audit.disagreements[0]
    assert found.number == 1
    assert found.independent_answer == "B"
    assert "Chloroplast stroma" in found.key_answer
    assert "thylakoid" in found.reason.lower()


async def test_the_flag_travels_with_the_question() -> None:
    """A report the teacher might not open is not enough; mark the question itself."""
    paper = _paper(THE_BAD_QUESTION)
    provider = _verifier([{"number": 1, "answer": "B", "confidence": 0.9, "reason": "Thylakoid."}])

    audit = await verify.verify_answers(provider, SETTINGS, paper)
    verify.annotate(paper, audit)

    question = paper.questions[0]
    assert question.needs_review is True
    # The note must be actionable on its own: both answers, and what to look for.
    note = question.review_note
    assert "B" in note and "Chloroplast stroma" in note
    assert "check" in note.lower()
    assert "defensible" in note, "the note should name the commonest cause, not just disagree"


async def test_the_warning_reaches_the_printed_answer_key() -> None:
    from app.render import markdown as md

    paper = _paper(THE_BAD_QUESTION)
    provider = _verifier([{"number": 1, "answer": "B", "confidence": 0.9, "reason": "Thylakoid."}])
    verify.annotate(paper, await verify.verify_answers(provider, SETTINGS, paper))

    key = md.paper_to_markdown(paper, answer_key=True)
    student_copy = md.paper_to_markdown(paper)

    assert "Check this answer" in key
    # The student's paper must not hint that anything is disputed.
    assert "Check this answer" not in student_copy


async def test_agreement_leaves_the_paper_untouched() -> None:
    paper = _paper(A_GOOD_QUESTION)
    provider = _verifier([{"number": 1, "answer": "A", "confidence": 1.0, "reason": "Rubisco."}])

    audit = await verify.verify_answers(provider, SETTINGS, paper)
    verify.annotate(paper, audit)

    assert audit.ok
    assert audit.agreed == 1
    assert paper.questions[0].needs_review is False
    assert "verified" in audit.summary()


# --------------------------------------------------------------------------- comparison


@pytest.mark.parametrize(
    ("given", "agrees"),
    [("A", True), ("a", True), ("A.", True), ("Option A", True), ("B", False)],
)
async def test_letter_answers_are_compared_tolerantly(given: str, agrees: bool) -> None:
    """Models write the letter a dozen ways; only a real disagreement should flag."""
    paper = _paper(A_GOOD_QUESTION)
    provider = _verifier([{"number": 1, "answer": given, "confidence": 1.0, "reason": ""}])
    audit = await verify.verify_answers(provider, SETTINGS, paper)
    assert audit.ok is agrees


async def test_numerical_answers_use_the_tolerance() -> None:
    question = Question(
        type=QuestionType.NUMERICAL,
        text="Compute the rate.",
        topic="Rates",
        numeric_answer=9.81,
        tolerance=0.05,
        unit="m/s^2",
    )
    paper = _paper(question)

    close = _verifier([{"number": 1, "answer": "9.8", "confidence": 1.0, "reason": ""}])
    assert (await verify.verify_answers(close, SETTINGS, paper)).ok

    paper = _paper(question.model_copy(deep=True))
    far = _verifier([{"number": 1, "answer": "4.9", "confidence": 1.0, "reason": ""}])
    assert not (await verify.verify_answers(far, SETTINGS, paper)).ok


async def test_true_false_is_compared() -> None:
    question = Question(
        type=QuestionType.TRUE_FALSE,
        text="The Calvin cycle requires darkness.",
        topic="Calvin",
        correct_bool=False,
    )
    provider = _verifier([{"number": 1, "answer": "True", "confidence": 0.9, "reason": "x"}])
    audit = await verify.verify_answers(provider, SETTINGS, _paper(question))
    assert not audit.ok


async def test_multi_select_compares_the_whole_set() -> None:
    question = Question(
        type=QuestionType.MULTI_SELECT,
        text="Select the products of the light reactions.",
        topic="Light",
        options=[
            Option(text="ATP", is_correct=True),
            Option(text="NADPH", is_correct=True),
            Option(text="Glucose", is_correct=False),
        ],
    )
    same = _verifier([{"number": 1, "answer": "A, B", "confidence": 1.0, "reason": ""}])
    assert (await verify.verify_answers(same, SETTINGS, _paper(question))).ok

    partial = _verifier([{"number": 1, "answer": "A", "confidence": 1.0, "reason": ""}])
    assert not (
        await verify.verify_answers(partial, SETTINGS, _paper(question.model_copy(deep=True)))
    ).ok


# --------------------------------------------------------------------------- robustness


async def test_written_questions_are_skipped_not_guessed_at() -> None:
    """An essay has many right phrasings; disagreement there would be noise."""
    essay = Question(
        type=QuestionType.LONG_ANSWER,
        text="Evaluate the claim that photosynthesis reverses respiration.",
        topic="Overview",
        marks=10,
        answer="They differ in pathway and location...",
    )
    audit = await verify.verify_answers(FakeProvider(), SETTINGS, _paper(essay))

    assert audit.checked == 0
    assert audit.skipped == 1
    assert audit.ok  # nothing checked is not the same as something wrong


async def test_the_verifier_never_sees_which_option_is_marked() -> None:
    """Showing the key would turn a second opinion into a rubber stamp."""
    paper = _paper(THE_BAD_QUESTION)
    provider = _verifier([{"number": 1, "answer": "A", "confidence": 1.0, "reason": ""}])
    await verify.verify_answers(provider, SETTINGS, paper)

    sent = "\n".join(m["content"] for call in provider.calls for m in call["messages"])
    assert "Chloroplast stroma" in sent  # the options are shown
    assert "is_correct" not in sent  # but not which one is right
    assert "correct:" not in sent.lower()


async def test_a_failing_verification_does_not_block_the_paper() -> None:
    """Verification is a safeguard, not a gate. A broken check must not lose the work."""
    provider = FakeProvider(scripted=["not json"] * 10)
    audit = await verify.verify_answers(provider, SETTINGS, _paper(A_GOOD_QUESTION))

    assert audit.checked == 0
    assert audit.skipped == 1


async def test_an_empty_paper_is_handled() -> None:
    audit = await verify.verify_answers(FakeProvider(), SETTINGS, QuestionPaper())
    assert audit.checked == 0
    assert audit.ok


async def test_questions_are_batched_not_asked_one_at_a_time() -> None:
    questions = [_mcq(f"Question {i}?", [("Right", True), ("Wrong", False)]) for i in range(12)]
    answers = [{"number": i + 1, "answer": "A", "confidence": 1.0, "reason": ""} for i in range(12)]
    provider = FakeProvider(
        scripted=[
            json.dumps({"answers": answers[i : i + verify.BATCH]})
            for i in range(0, 12, verify.BATCH)
        ]
    )

    audit = await verify.verify_answers(provider, SETTINGS, _paper(*questions))

    assert audit.checked == 12
    assert len(provider.calls) == 3  # ceil(12 / 5)


# --------------------------------------------------------------------------- verifier model


def test_a_separate_verifier_model_can_be_configured() -> None:
    """Checking with the same weights that wrote the question is a weaker check.

    A model that believes the light reactions happen in the stroma believes it twice, so
    the verifier can be pointed at different weights. Unset, it reuses the generator.
    """
    from app.config import get_settings
    from app.llm.registry import build_for_model

    settings = get_settings()

    same = build_for_model(settings, "")
    assert same.model == settings.llm_model, "blank must reuse the generation model"

    also_same = build_for_model(settings, settings.llm_model)
    assert also_same is same, "naming the same model must not build a second provider"

    other = build_for_model(settings, "some-other-model")
    assert other.model == "some-other-model"
    assert other.base_url == same.base_url, "everything else must be configured alike"


async def test_the_audit_records_which_model_checked() -> None:
    """A teacher should be able to tell whether the second opinion was truly independent."""
    from app.generators.verify import AnswerAudit

    audit = AnswerAudit(checked=3, agreed=3, verifier_model="mistral:7b-instruct")
    assert audit.verifier_model == "mistral:7b-instruct"
    assert AnswerAudit().verifier_model == ""
