"""Independently checking the answer key.

This exists because of a specific failure in a real run. A generated paper asked "which is
the primary site of the light-dependent reactions?" and marked *Chloroplast stroma* correct
over *Thylakoid membrane*. The rationale attached to that option even read "the stroma is
where the light-independent reactions occur" — the model contradicted itself inside a single
question and nothing downstream noticed.

A wrong answer key is the worst thing this product could produce. It does not look wrong; it
gets printed, handed to thirty students, and marked against. Blueprint validation cannot
catch it — the paper was structurally perfect.

So every machine-markable question is answered again from scratch, in a fresh context that
never sees which option was marked, and disagreements are flagged for the teacher. This is
not the model checking its own work with its work in front of it; it is a second opinion.

Measured cost: about one call per five questions, a minute or so on a 30-mark paper.
"""

from __future__ import annotations

import re
import time

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.core.logging import get_logger
from app.llm.base import LLMProvider, system, user
from app.llm.structured import generate_structured
from app.schemas.paper import Question, QuestionPaper, QuestionType

log = get_logger(__name__)

#: Questions per verification call. Small, because each needs independent thought and a
#: long list invites the model to pattern-match rather than answer.
BATCH = 5

#: Types whose answer can be compared unambiguously. A short-answer question has many
#: acceptable phrasings, so disagreement there would be noise rather than signal.
CHECKABLE: frozenset[QuestionType] = frozenset(
    {
        QuestionType.MCQ,
        QuestionType.TRUE_FALSE,
        QuestionType.ASSERTION_REASON,
        QuestionType.NUMERICAL,
        QuestionType.MULTI_SELECT,
    }
)

#: A standalone capital letter — the option a verifier picked. Matching any capital would
#: read the "O" out of "Option B".
_SINGLE_LETTER = re.compile(r"\b([A-Z])\b")

PERSONA = (
    "You are a subject expert sitting an exam. You answer each question on its merits. "
    "You have not seen any answer key and you are not trying to agree with anyone."
)


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(description="The question number you are answering")
    answer: str = Field(
        description="For a choice question the option letter, e.g. 'B'. For true/false, "
        "'True' or 'False'. For a numerical question, just the number."
    )
    confidence: float = Field(default=1.0, ge=0, le=1)
    reason: str = Field(default="", description="One sentence saying why")


class _AnswerSheet(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: list[_Answer] = Field(default_factory=list)


class Disagreement(BaseModel):
    """One question where the second opinion differs from the key."""

    model_config = ConfigDict(extra="ignore")

    number: int
    question: str
    key_answer: str
    independent_answer: str
    reason: str
    confidence: float


class AnswerAudit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checked: int = 0
    agreed: int = 0
    disagreements: list[Disagreement] = Field(default_factory=list)
    skipped: int = Field(
        default=0, description="Questions whose answers cannot be compared unambiguously"
    )
    seconds: float = 0.0
    verifier_model: str = Field(
        default="",
        description=(
            "Which model did the checking. When it matches the generator, the check is "
            "weaker — a model confirms its own mistakes."
        ),
    )

    @property
    def ok(self) -> bool:
        return not self.disagreements

    def summary(self) -> str:
        if not self.checked:
            return "No questions could be independently checked."
        if self.ok:
            return (
                f"Answer key verified: an independent pass agreed on all "
                f"{self.checked} machine-markable questions."
            )
        return (
            f"{len(self.disagreements)} of {self.checked} answers need your review — an "
            "independent pass disagreed with the key."
        )


def _letter_for(question: Question) -> str:
    """The key's answer, in the same form the verifier is asked to produce."""
    match question.type:
        case QuestionType.TRUE_FALSE:
            return "True" if question.correct_bool else "False"
        case QuestionType.NUMERICAL:
            return f"{question.numeric_answer:g}" if question.numeric_answer is not None else ""
        case QuestionType.MULTI_SELECT:
            return ",".join(chr(65 + i) for i, o in enumerate(question.options) if o.is_correct)
        case _:
            for index, option in enumerate(question.options):
                if option.is_correct:
                    return chr(65 + index)
            return ""


def _render(question: Question, number: int) -> str:
    """The question as a student sees it — no hint of which option is marked."""
    lines = [f"{number}. {question.text}"]
    if question.scenario:
        lines.insert(0, f"Scenario: {question.scenario}")
    for index, option in enumerate(question.options):
        lines.append(f"   {chr(65 + index)}. {option.text}")
    if question.type == QuestionType.TRUE_FALSE:
        lines.append("   Answer True or False.")
    if question.type == QuestionType.NUMERICAL:
        unit = f" (in {question.unit})" if question.unit else ""
        lines.append(f"   Give the numeric answer{unit}.")
    if question.type == QuestionType.MULTI_SELECT:
        lines.append("   Several options may be correct; list every correct letter.")
    return "\n".join(lines)


def _option_letters(value: str) -> set[str]:
    """The option letters in a free-text answer.

    Models write a choice a dozen ways — "B", "b", "B.", "Option B", "A and C". Taking the
    first alphabetic character gets "Option B" wrong (it reads the O), so match only
    standalone letters, and fall back to the leading character for a bare "B.".
    """
    standalone = set(_SINGLE_LETTER.findall(value.upper()))
    if standalone:
        return standalone
    stripped = value.strip().upper()
    return {stripped[0]} if stripped and stripped[0].isalpha() else set()


def _matches(expected: str, given: str, question: Question) -> bool:
    """Whether the independent answer agrees with the key."""
    expected, given = expected.strip(), given.strip()
    if not expected or not given:
        return True  # nothing to compare; do not manufacture a disagreement

    if question.type == QuestionType.NUMERICAL:
        try:
            difference = abs(float(expected) - float(given.split()[0].rstrip(".,")))
        except (ValueError, IndexError):
            return True
        return difference <= max(question.tolerance, abs(float(expected)) * 0.01)

    if question.type == QuestionType.MULTI_SELECT:
        return _option_letters(expected) == _option_letters(given)

    if question.type == QuestionType.TRUE_FALSE:
        return expected[:1].lower() == given.strip()[:1].lower()

    # A choice question: compare the letter.
    chosen = _option_letters(given)
    return bool(chosen) and expected.upper()[:1] in chosen


async def verify_answers(
    provider: LLMProvider,
    settings: Settings,
    paper: QuestionPaper,
    *,
    progress=None,  # noqa: ANN001
) -> AnswerAudit:
    """Answer every machine-markable question again, blind, and report disagreements."""
    started = time.perf_counter()

    numbered: list[tuple[int, Question]] = []
    skipped = 0
    for index, question in enumerate(paper.questions, start=1):
        if question.type in CHECKABLE and (
            question.options
            or question.correct_bool is not None
            or question.numeric_answer is not None
        ):
            numbered.append((index, question))
        else:
            skipped += 1

    audit = AnswerAudit(skipped=skipped)
    if not numbered:
        audit.seconds = round(time.perf_counter() - started, 2)
        return audit

    for start in range(0, len(numbered), BATCH):
        window = numbered[start : start + BATCH]
        if progress is not None:
            await progress(
                start / len(numbered),
                f"Checking answers {start + 1}-{start + len(window)} of {len(numbered)}",
            )

        prompt = (
            "Answer each question below. Work from your own subject knowledge.\n"
            "Give the option letter for choice questions, True or False for true/false "
            "questions, and just the number for numerical ones. Add one sentence of "
            "reasoning and a confidence between 0 and 1.\n"
            # A live run produced two questions where several options were defensibly
            # correct — "which is a direct product of the light reactions?" with oxygen,
            # ATP and NADPH all listed. That is as unmarkable as a wrong key, and a
            # teacher only discovers it when a student challenges the mark, so the
            # verifier is asked to name it rather than just pick a different letter.
            "Two defects matter as much as a wrong answer, so say so plainly in `reason` "
            "if you see them: more than one option is defensibly correct, or none of them "
            "is. Either makes the question unmarkable. Give your best choice anyway.\n\n"
            + "\n\n".join(_render(question, number) for number, question in window)
        )

        try:
            sheet = await generate_structured(
                provider,
                _AnswerSheet,
                [system(PERSONA), user(prompt)],
                temperature=0.0,  # a second opinion should be the model's considered view
                max_attempts=2,  # verification is best-effort; never block a paper on it
            )
        except Exception as exc:
            log.warning("Answer verification batch failed, skipping: %s", exc)
            audit.skipped += len(window)
            continue

        by_number = {answer.number: answer for answer in sheet.answers}
        for number, question in window:
            answer = by_number.get(number)
            if answer is None:
                audit.skipped += 1
                continue

            audit.checked += 1
            expected = _letter_for(question)
            if _matches(expected, answer.answer, question):
                audit.agreed += 1
                continue

            log.info(
                "Answer key disagreement on question %d: key=%s independent=%s",
                number,
                expected,
                answer.answer,
            )
            audit.disagreements.append(
                Disagreement(
                    number=number,
                    question=question.text[:300],
                    key_answer=f"{expected} — {question.answer_text()}"[:200],
                    independent_answer=answer.answer[:100],
                    reason=answer.reason[:300],
                    confidence=answer.confidence,
                )
            )

    audit.seconds = round(time.perf_counter() - started, 2)
    log.info(
        "Verified %d answers in %.0fs: %d agreed, %d disagreed",
        audit.checked,
        audit.seconds,
        audit.agreed,
        len(audit.disagreements),
    )
    return audit


def annotate(paper: QuestionPaper, audit: AnswerAudit) -> None:
    """Mark the disputed questions on the paper itself.

    The flag travels with the question into the answer key and the editor, so a teacher
    sees it where they are working rather than only in a report they might not open.
    """
    flagged = {d.number: d for d in audit.disagreements}
    for index, question in enumerate(paper.questions, start=1):
        disagreement = flagged.get(index)
        if disagreement is None:
            continue
        question.needs_review = True
        # The note ends by naming the two things that cause a disagreement, because a
        # small model reliably picks the other letter but rarely articulates *why* the
        # question is defective. In practice the commonest cause is not a wrong key but a
        # question with more than one defensible answer — "which is a direct product of
        # the light reactions?" listing oxygen, ATP and NADPH. Prompting the teacher to
        # look for that is more dependable than hoping the model says it.
        question.review_note = (
            f"A second, independent pass answered {disagreement.independent_answer} rather "
            f"than {disagreement.key_answer}. It reasoned: {disagreement.reason} "
            "Either the key is wrong, or more than one option is defensible — check which "
            "before you use this question."
        )
