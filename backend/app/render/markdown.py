"""Markdown rendering for every artifact type.

Markdown is the universal intermediate here: it is what a teacher can paste into an LMS,
what the HTML and PDF renderers build from, and what makes every artifact diffable. Each
function is a pure transformation from a schema instance to a string, so they are trivial
to test and never need a model.
"""

from __future__ import annotations

from app.schemas.deck import Deck, SlideLayout
from app.schemas.lesson import LessonPlan
from app.schemas.notes import LectureNotes, NoteSection
from app.schemas.paper import Question, QuestionPaper, QuestionType
from app.schemas.rubric import Rubric, RubricStyle
from app.schemas.study import Flashcard, FlashcardDeck, GradingResult, Worksheet


def _join(parts: list[str]) -> str:
    return "\n".join(p for p in parts if p is not None).strip() + "\n"


def _table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in padded[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in padded[1:]:
        lines.append(
            "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in row) + " |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- slides


def deck_to_markdown(deck: Deck, *, include_notes: bool = True) -> str:
    """A deck as Markdown. Slides are separated by ``---`` so the result is also a valid
    reveal.js / Marp / Pandoc source file."""
    parts = [f"# {deck.title}", ""]
    if deck.subtitle:
        parts += [f"*{deck.subtitle}*", ""]
    if deck.presenter:
        parts += [deck.presenter, ""]

    if deck.objectives:
        parts += ["## Learning objectives", ""]
        parts += [f"- {o.text} *({o.bloom.value})*" for o in deck.objectives]
        parts += [""]

    for index, slide in enumerate(deck.slides, start=1):
        parts += ["---", ""]
        if slide.layout == SlideLayout.SECTION:
            parts += [f"# {slide.heading}", ""]
        else:
            parts += [f"## {index}. {slide.heading}", ""]
        if slide.subheading:
            parts += [f"*{slide.subheading}*", ""]

        if slide.quote:
            parts += [f"> {slide.quote}", ""]
            if slide.quote_attribution:
                parts += [f"> — {slide.quote_attribution}", ""]

        parts += [f"- {b}" for b in slide.bullets]
        if slide.bullets:
            parts += [""]

        for column in slide.columns:
            if column.heading:
                parts += [f"**{column.heading}**", ""]
            parts += [f"- {b}" for b in column.bullets] + [""]

        if slide.table_markdown:
            parts += [slide.table_markdown, ""]

        if slide.figure and slide.figure.spec:
            parts += [
                f"```mermaid\n{slide.figure.spec}\n```",
                f"*{slide.figure.caption}*" if slide.figure.caption else "",
                "",
            ]

        if include_notes and slide.speaker_notes:
            parts += ["**Speaker notes:** " + slide.speaker_notes, ""]

        if slide.citations:
            parts += ["*Sources: " + "; ".join(c.label() for c in slide.citations) + "*", ""]

    return _join(parts)


# --------------------------------------------------------------------------- notes


def notes_to_markdown(notes: LectureNotes) -> str:
    parts = [f"# {notes.title}", ""]
    if notes.subtitle:
        parts += [f"*{notes.subtitle}*", ""]
    if notes.overview:
        parts += [notes.overview, ""]

    if notes.objectives:
        parts += ["## Learning objectives", ""]
        parts += [f"{i}. {o.text}" for i, o in enumerate(notes.objectives, start=1)]
        parts += [""]

    if notes.prerequisites:
        parts += ["## Before you start", ""]
        parts += [f"- {p}" for p in notes.prerequisites] + [""]

    for section in notes.sections:
        parts += _section_to_markdown(section, level=2)

    if notes.summary:
        parts += ["## Summary", "", notes.summary, ""]

    if notes.glossary:
        parts += ["## Glossary", ""]
        parts += [
            _table([["Term", "Definition"]] + [[t.term, t.definition] for t in notes.glossary]),
            "",
        ]

    if notes.review_questions:
        parts += ["## Review questions", ""]
        for index, question in enumerate(notes.review_questions, start=1):
            parts += [f"{index}. {question.question}"]
        parts += [""]
        if any(q.answer for q in notes.review_questions):
            parts += ["<details><summary>Answers</summary>", ""]
            parts += [
                f"{i}. {q.answer}"
                for i, q in enumerate(notes.review_questions, start=1)
                if q.answer
            ]
            parts += ["", "</details>", ""]

    if notes.further_reading:
        parts += ["## Further reading", ""]
        parts += [f"- {r}" for r in notes.further_reading] + [""]

    if notes.citations:
        parts += ["## Sources", ""]
        parts += [f"- {c.label()}" for c in notes.citations] + [""]

    return _join(parts)


def _section_to_markdown(section: NoteSection, level: int) -> list[str]:
    hashes = "#" * min(level, 6)
    parts = [f"{hashes} {section.heading}", ""]
    if section.body:
        parts += [section.body, ""]

    if section.key_terms:
        parts += [f"{'#' * min(level + 1, 6)} Key terms", ""]
        for term in section.key_terms:
            line = f"- **{term.term}** — {term.definition}"
            if term.example:
                line += f" *(e.g. {term.example})*"
            parts.append(line)
        parts += [""]

    for example in section.examples:
        parts += [f"> **Worked example.** {example.prompt}", ""]
        parts += [f"> {i}. {step}" for i, step in enumerate(example.steps, start=1)]
        if example.answer:
            parts += ["> ", f"> **Answer:** {example.answer}"]
        if example.commentary:
            parts += ["> ", f"> {example.commentary}"]
        parts += [""]

    for callout in section.callouts:
        label = callout.title or callout.kind.value.replace("_", " ").title()
        parts += [f"> **{label}.** {callout.body}", ""]

    for figure in section.figures:
        if figure.spec:
            parts += [f"```mermaid\n{figure.spec}\n```"]
        if figure.caption:
            parts += [f"*{figure.caption}*"]
        parts += [""]

    for subsection in section.subsections:
        parts += _section_to_markdown(subsection, level + 1)

    return parts


# --------------------------------------------------------------------------- exams


def paper_to_markdown(paper: QuestionPaper, *, answer_key: bool = False) -> str:
    """The paper as printed, or the answer key when ``answer_key`` is set."""
    meta = paper.meta
    parts: list[str] = []

    if meta.institution:
        parts += [f"**{meta.institution}**", ""]
    title = meta.exam_name or "Examination"
    if answer_key:
        title += " — Answer Key"
    if meta.variant_label:
        title += f" ({meta.variant_label})"
    parts += [f"# {title}", ""]

    header = [
        f"**Course:** {meta.course}" if meta.course else "",
        f"**Class:** {meta.grade_level}" if meta.grade_level else "",
        f"**Time:** {meta.duration_minutes} minutes" if meta.duration_minutes else "",
        f"**Maximum marks:** {paper.total_marks:g}",
        f"**Date:** {meta.date}" if meta.date else "",
    ]
    parts += [" · ".join(h for h in header if h), ""]

    if meta.instructions and not answer_key:
        parts += ["## Instructions", ""]
        parts += [f"{i}. {line}" for i, line in enumerate(meta.instructions, start=1)]
        parts += [""]

    number = 0
    for section in paper.sections:
        parts += [f"## {section.title}", ""]
        if section.instructions and not answer_key:
            parts += [f"*{section.instructions}*", ""]
        if section.choose_count:
            parts += [f"*Answer any {section.choose_count} questions from this section.*", ""]

        for question in section.questions:
            number += 1
            parts += _question_to_markdown(question, number, answer_key=answer_key)

    if not answer_key:
        parts += ["---", "", "*End of paper*", ""]
    return _join(parts)


def _question_to_markdown(question: Question, number: int, *, answer_key: bool) -> list[str]:
    marks = f"**[{question.marks:g}]**"
    parts = [f"**{number}.** {question.text} {marks}", ""]

    if question.scenario:
        parts += [f"> {question.scenario}", ""]

    if question.options:
        parts += [
            f"   {chr(ord('A') + i)}. {option.text}" for i, option in enumerate(question.options)
        ]
        parts += [""]

    if question.pairs:
        parts += [
            _table(
                [["", "Column A", "", "Column B"]]
                + [
                    [f"{i + 1}.", p.left, f"{chr(ord('a') + i)}.", p.right]
                    for i, p in enumerate(question.pairs)
                ]
            ),
            "",
        ]

    for sub in question.sub_questions:
        parts += [f"   **({sub.label or '·'})** {sub.text} *[{sub.marks:g}]*"]
        if sub.options:
            parts += [f"      {chr(ord('A') + i)}. {o.text}" for i, o in enumerate(sub.options)]
    if question.sub_questions:
        parts += [""]

    if question.figure and question.figure.spec:
        parts += [f"```mermaid\n{question.figure.spec}\n```", ""]

    if answer_key:
        if question.needs_review:
            parts += [
                "   > ⚠️ **Check this answer.** " + question.review_note,
                "",
            ]
        parts += [f"   ✓ **Answer:** {question.answer_text()}", ""]
        if question.working:
            parts += ["   **Working:**", ""]
            parts += [f"   - {line}" for line in question.working.splitlines() if line.strip()]
            parts += [""]
        if question.rubric_points:
            parts += ["   **Mark scheme:**", ""]
            parts += [
                f"   - {point.description} *({point.marks:g})*" for point in question.rubric_points
            ]
            parts += [""]
        if question.explanation:
            parts += [f"   *{question.explanation}*", ""]
        if question.type in (QuestionType.MCQ, QuestionType.MULTI_SELECT):
            rationales = [o for o in question.options if o.rationale and not o.is_correct]
            if rationales:
                parts += ["   **Why the distractors are wrong:**", ""]
                parts += [
                    f"   - {chr(ord('A') + question.options.index(o))}. {o.rationale}"
                    for o in rationales
                ]
                parts += [""]
        parts += [
            f"   <sub>{question.topic} · {question.bloom.value} · {question.difficulty.value}</sub>",
            "",
        ]

    return parts


# --------------------------------------------------------------------------- worksheet


def worksheet_to_markdown(worksheet: Worksheet, *, answer_key: bool = False) -> str:
    parts = [f"# {worksheet.title}", ""]
    header = [worksheet.subject, worksheet.grade_level, f"{worksheet.estimated_minutes} minutes"]
    parts += [" · ".join(h for h in header if h), ""]

    if not answer_key:
        parts += ["Name: ______________________________    Date: ______________", ""]

    if worksheet.objectives:
        parts += ["**In this worksheet you will:**", ""]
        parts += [f"- {o.text}" for o in worksheet.objectives] + [""]

    if worksheet.warm_up and not answer_key:
        parts += ["## Warm up", ""]
        parts += [f"{i}. {w}" for i, w in enumerate(worksheet.warm_up, start=1)] + [""]

    if worksheet.worked_example:
        parts += ["## Worked example", "", worksheet.worked_example, ""]

    number = 0
    for section in worksheet.sections:
        parts += [f"## {section.title}", ""]
        if section.instructions:
            parts += [f"*{section.instructions}*", ""]
        for question in section.questions:
            number += 1
            parts += _question_to_markdown(question, number, answer_key=answer_key)
            if section.answer_lines and not answer_key:
                parts += ["   " + "_" * 60 for _ in range(section.answer_lines)] + [""]

    if worksheet.challenge:
        parts += ["## Challenge", ""]
        for question in worksheet.challenge:
            number += 1
            parts += _question_to_markdown(question, number, answer_key=answer_key)

    if worksheet.reflection_prompt and not answer_key:
        parts += ["## Reflect", "", worksheet.reflection_prompt, "", "_" * 60, ""]

    return _join(parts)


# --------------------------------------------------------------------------- rubric


def rubric_to_markdown(rubric: Rubric) -> str:
    parts = [f"# {rubric.title}", ""]
    if rubric.task_description:
        parts += [rubric.task_description, ""]
    parts += [f"**Total:** {rubric.total_points:g} points · **Style:** {rubric.style.value}", ""]

    if rubric.style == RubricStyle.HOLISTIC:
        parts += [
            _table(
                [["Level", "What this looks like"]]
                + [[c.level_name, c.descriptor] for c in rubric.holistic_descriptors]
            ),
            "",
        ]
    else:
        parts += [_table(rubric.grid()), ""]

    if rubric.levels and any(level.description for level in rubric.levels):
        parts += ["## Performance levels", ""]
        parts += [
            f"- **{level.name}** ({level.weight:.0%}) — {level.description}"
            for level in rubric.levels
        ]
        parts += [""]

    if rubric.student_facing_summary:
        parts += ["## What this means for you", "", rubric.student_facing_summary, ""]

    return _join(parts)


# --------------------------------------------------------------------------- lesson plan


def lesson_plan_to_markdown(plan: LessonPlan) -> str:
    parts = [f"# {plan.title}", ""]
    header = [
        plan.subject,
        plan.grade_level,
        f"{plan.duration_minutes} minutes",
        plan.date,
        plan.template.value.replace("_", " "),
    ]
    parts += [" · ".join(h for h in header if h), ""]

    if plan.objectives:
        parts += ["## Learning objectives", ""]
        parts += [f"- {o.text} *({o.bloom.value})*" for o in plan.objectives] + [""]

    if plan.success_criteria:
        parts += ["## Success criteria", ""]
        parts += [f"- {c}" for c in plan.success_criteria] + [""]

    if plan.prerequisites:
        parts += ["**Prior knowledge:** " + "; ".join(plan.prerequisites), ""]
    if plan.key_vocabulary:
        parts += ["**Key vocabulary:** " + ", ".join(plan.key_vocabulary), ""]
    if plan.materials:
        parts += ["## Materials", ""] + [f"- {m}" for m in plan.materials] + [""]

    if plan.hook:
        parts += ["## Hook", "", plan.hook, ""]

    if plan.activities:
        parts += ["## Lesson sequence", ""]
        parts += [
            _table(
                [["Time", "Activity", "Teacher", "Students", "Grouping"]]
                + [
                    [
                        f"{a.minutes} min",
                        f"**{a.name}**<br>*{a.kind.value.replace('_', ' ')}*",
                        a.teacher_does,
                        a.students_do,
                        a.grouping,
                    ]
                    for a in plan.activities
                ]
            ),
            "",
        ]
        checks = [a for a in plan.activities if a.check_for_understanding]
        if checks:
            parts += ["**Checks for understanding**", ""]
            parts += [f"- *{a.name}*: {a.check_for_understanding}" for a in checks] + [""]

    if plan.closure:
        parts += ["## Closure", "", plan.closure, ""]

    diff = plan.differentiation
    if any([diff.support, diff.core, diff.extension, diff.language_support, diff.accessibility]):
        parts += ["## Differentiation", ""]
        for label, items in (
            ("Support", diff.support),
            ("Core", diff.core),
            ("Extension", diff.extension),
            ("Language support", diff.language_support),
            ("Accessibility", diff.accessibility),
        ):
            if items:
                parts += [f"**{label}**", ""] + [f"- {i}" for i in items] + [""]

    if plan.anticipated_misconceptions:
        parts += ["## Anticipated misconceptions", ""]
        parts += [f"- {m}" for m in plan.anticipated_misconceptions] + [""]

    if plan.formative_assessment:
        parts += ["## Assessment", ""]
        parts += [f"- {a}" for a in plan.formative_assessment] + [""]
    if plan.summative_assessment:
        parts += [f"**Summative:** {plan.summative_assessment}", ""]
    if plan.homework:
        parts += ["## Homework", "", plan.homework, ""]
    if plan.teacher_notes:
        parts += ["## Notes to self", "", plan.teacher_notes, ""]

    return _join(parts)


# --------------------------------------------------------------------------- flashcards


def flashcards_to_markdown(deck: FlashcardDeck) -> str:
    parts = [f"# {deck.title}", ""]
    if deck.description:
        parts += [deck.description, ""]
    parts += [f"{len(deck.cards)} cards", ""]
    parts += [
        _table(
            [["Front", "Back", "Tags"]]
            + [[_card_front(c), c.back, ", ".join(c.tags)] for c in deck.cards]
        ),
        "",
    ]
    return _join(parts)


def _card_front(card: Flashcard) -> str:
    return card.front


# --------------------------------------------------------------------------- grading


def grading_to_markdown(result: GradingResult) -> str:
    parts = [f"# Feedback — {result.task_title or 'Assessment'}", ""]
    if result.student_identifier:
        parts += [f"**Student:** {result.student_identifier}", ""]
    parts += [
        f"**Score:** {result.total_points:g} / {result.max_points:g} "
        f"({result.percentage:g}%)" + (f" · **Grade:** {result.grade}" if result.grade else ""),
        "",
    ]

    if result.needs_teacher_review:
        parts += [
            "> ⚠️ **This marking needs a teacher's review.** "
            + (result.review_reason or "The work is borderline or ambiguous."),
            "",
        ]

    if result.scores:
        parts += ["## Breakdown", ""]
        parts += [
            _table(
                [["Criterion", "Level", "Marks", "Why"]]
                + [
                    [s.criterion, s.level, f"{s.points:g}/{s.max_points:g}", s.justification]
                    for s in result.scores
                ]
            ),
            "",
        ]

    if result.strengths:
        parts += ["## What went well", ""] + [f"- {s}" for s in result.strengths] + [""]
    if result.areas_to_improve:
        parts += ["## What to work on", ""] + [f"- {a}" for a in result.areas_to_improve] + [""]
    if result.next_steps:
        parts += ["## Next steps", ""] + [f"- {n}" for n in result.next_steps] + [""]
    if result.feedback_to_student:
        parts += ["## Feedback", "", result.feedback_to_student, ""]

    parts += [
        "",
        f"<sub>Marked with AI assistance (confidence {result.confidence:.0%}). "
        "A teacher should confirm before this is returned to the student.</sub>",
        "",
    ]
    return _join(parts)
