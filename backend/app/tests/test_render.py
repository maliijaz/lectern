"""Renderer tests.

Renderers are pure functions from a schema instance to bytes, so these run with no model
and no network. The checks are deliberately about *structure* — that a .pptx opens and has
the right slide count, that Moodle XML parses and every question survived, that the answer
key contains answers and the student paper does not. Visual polish is not testable here;
silent data loss is, and that is the failure that matters.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from app.render import anki, docx, lms, markdown, pptx
from app.schemas.common import Bloom, Difficulty, KeyTerm, LearningObjective
from app.schemas.deck import Deck, Slide, SlideColumn, SlideLayout
from app.schemas.lesson import Activity, LessonPlan
from app.schemas.notes import Callout, LectureNotes, NoteSection, ReviewQuestion, WorkedExample
from app.schemas.paper import (
    MatchPair,
    Option,
    PaperMeta,
    PaperSection,
    Question,
    QuestionPaper,
    QuestionType,
    RubricPoint,
    SubQuestion,
)
from app.schemas.rubric import Criterion, CriterionLevel, PerformanceLevel, Rubric
from app.schemas.study import (
    CardKind,
    CriterionScore,
    Flashcard,
    FlashcardDeck,
    GradingResult,
    Worksheet,
    WorksheetSection,
)

# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def deck() -> Deck:
    return Deck(
        title="Photosynthesis",
        subtitle="Biology · Grade 10",
        presenter="Ms Khan",
        objectives=[LearningObjective(text="Explain the light reactions", bloom=Bloom.UNDERSTAND)],
        slides=[
            Slide(layout=SlideLayout.TITLE, heading="Photosynthesis", subheading="Grade 10"),
            Slide(
                layout=SlideLayout.BULLETS,
                heading="Why light matters",
                bullets=["Photons excite electrons", "Water is split", "Oxygen is released"],
                speaker_notes="Ask the class what happens at night.",
            ),
            Slide(
                layout=SlideLayout.TWO_COLUMN,
                heading="Two stages",
                columns=[
                    SlideColumn(heading="Light", bullets=["Thylakoid", "Needs photons"]),
                    SlideColumn(heading="Calvin", bullets=["Stroma", "Fixes carbon"]),
                ],
            ),
            Slide(
                layout=SlideLayout.TABLE,
                heading="Comparison",
                table_markdown="| Stage | Where |\n|---|---|\n| Light | Thylakoid |\n| Calvin | Stroma |",
            ),
            Slide(
                layout=SlideLayout.QUIZ,
                heading="Where does photolysis occur?",
                bullets=["Stroma", "Thylakoid", "Cytosol", "Nucleus"],
                speaker_notes="Answer: B",
            ),
            Slide(layout=SlideLayout.SECTION, heading="Part two"),
            Slide(
                layout=SlideLayout.QUOTE,
                heading="q",
                quote="Life is bottled sunshine.",
                quote_attribution="Wynwood Reade",
            ),
            Slide(layout=SlideLayout.SUMMARY, heading="Key points", bullets=["ATP", "Glucose"]),
        ],
        theme="academic",
    )


@pytest.fixture
def paper() -> QuestionPaper:
    return QuestionPaper(
        meta=PaperMeta(
            institution="Springfield High",
            course="Biology",
            exam_name="Mid-Term Examination",
            grade_level="Grade 10",
            duration_minutes=90,
            total_marks=30,
            instructions=["Answer all questions.", "Write clearly."],
        ),
        sections=[
            PaperSection(
                title="Section A — Objective Questions",
                instructions="Choose the best option.",
                questions=[
                    Question(
                        type=QuestionType.MCQ,
                        text="Where do the light reactions occur?",
                        marks=1,
                        topic="Light reactions",
                        bloom=Bloom.REMEMBER,
                        options=[
                            Option(
                                text="Thylakoid membrane",
                                is_correct=True,
                                rationale="Correct — the site of the electron transport chain.",
                            ),
                            Option(text="Stroma", rationale="Confuses it with the Calvin cycle."),
                            Option(text="Cytosol", rationale="Confuses it with glycolysis."),
                            Option(text="Nucleus", rationale="No photosynthetic machinery here."),
                        ],
                        explanation="Photosystems sit in the thylakoid membrane.",
                    ),
                    Question(
                        type=QuestionType.TRUE_FALSE,
                        text="The Calvin cycle requires darkness.",
                        marks=1,
                        topic="Calvin cycle",
                        correct_bool=False,
                        explanation="It does not require light directly, but darkness is not needed.",
                    ),
                    Question(
                        type=QuestionType.FILL_BLANK,
                        text="Carbon dioxide is fixed by the enzyme ____.",
                        marks=1,
                        topic="Calvin cycle",
                        blanks=["rubisco"],
                    ),
                    Question(
                        type=QuestionType.MATCHING,
                        text="Match each stage to its location.",
                        marks=4,
                        topic="Overview",
                        pairs=[
                            MatchPair(left="Light reactions", right="Thylakoid"),
                            MatchPair(left="Calvin cycle", right="Stroma"),
                            MatchPair(left="Glycolysis", right="Cytosol"),
                        ],
                    ),
                    Question(
                        type=QuestionType.MULTI_SELECT,
                        text="Select all products of the light reactions.",
                        marks=2,
                        topic="Light reactions",
                        options=[
                            Option(text="ATP", is_correct=True),
                            Option(text="NADPH", is_correct=True),
                            Option(text="Glucose"),
                            Option(text="Carbon dioxide"),
                        ],
                    ),
                ],
            ),
            PaperSection(
                title="Section B — Short Answer Questions",
                questions=[
                    Question(
                        type=QuestionType.NUMERICAL,
                        text="If 6 CO2 molecules are fixed, how many glucose molecules form?",
                        marks=3,
                        topic="Calvin cycle",
                        numeric_answer=1.0,
                        unit="molecules",
                        tolerance=0.01,
                        working="6 CO2 → 1 glucose\nStoichiometry of the Calvin cycle",
                    ),
                    Question(
                        type=QuestionType.SHORT_ANSWER,
                        text="Explain why oxygen is a by-product.",
                        marks=3,
                        topic="Light reactions",
                        answer="Water is split to replace electrons, releasing O2.",
                        keywords=["photolysis", "water"],
                        rubric_points=[RubricPoint(description="Mentions photolysis", marks=2)],
                    ),
                ],
            ),
            PaperSection(
                title="Section C — Long Answer Questions",
                choose_count=1,
                questions=[
                    Question(
                        type=QuestionType.LONG_ANSWER,
                        text="Evaluate the claim that photosynthesis is the reverse of respiration.",
                        marks=10,
                        topic="Overview",
                        bloom=Bloom.EVALUATE,
                        difficulty=Difficulty.HARD,
                        answer="Superficially similar, but the pathways and locations differ...",
                        rubric_points=[
                            RubricPoint(description="States both equations", marks=3),
                            RubricPoint(description="Identifies key differences", marks=4),
                            RubricPoint(description="Reaches a defended judgement", marks=3),
                        ],
                    ),
                    Question(
                        type=QuestionType.CASE_STUDY,
                        text="Answer the parts below.",
                        marks=5,
                        topic="Overview",
                        scenario="A greenhouse raises CO2 concentration from 400 to 800 ppm.",
                        sub_questions=[
                            SubQuestion(
                                label="a",
                                text="Predict the effect.",
                                marks=2,
                                answer="Rate increases",
                            ),
                            SubQuestion(
                                label="b",
                                text="Explain why it plateaus.",
                                marks=3,
                                answer="Another factor becomes limiting",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def notes() -> LectureNotes:
    return LectureNotes(
        title="Photosynthesis",
        subtitle="Biology · Grade 10",
        overview="Plants convert light energy into chemical energy.",
        objectives=[LearningObjective(text="Describe the Calvin cycle")],
        prerequisites=["Cell structure"],
        sections=[
            NoteSection(
                heading="Light reactions",
                body=(
                    "Photons strike **chlorophyll** and excite electrons.\n"
                    "- Water is split\n- Oxygen is released\n\nThe result is ATP and NADPH."
                ),
                key_terms=[KeyTerm(term="Photolysis", definition="The splitting of water")],
                callouts=[
                    Callout(
                        kind="misconception",
                        title="Not photosynthesis alone",
                        body="Plants respire too, all the time.",
                    )
                ],
                examples=[
                    WorkedExample(
                        prompt="How many photons per O2?",
                        steps=["Two photosystems", "Four electrons"],
                        answer="About 8",
                    )
                ],
            ),
            NoteSection(heading="The Calvin cycle", body="Carbon is fixed in the stroma."),
        ],
        summary="Light reactions capture energy; the Calvin cycle spends it.",
        glossary=[KeyTerm(term="Rubisco", definition="The carbon-fixing enzyme")],
        review_questions=[ReviewQuestion(question="Where is ATP made?", answer="Thylakoid")],
        further_reading=["Any plant physiology textbook"],
    )


@pytest.fixture
def worksheet(paper: QuestionPaper) -> Worksheet:
    return Worksheet(
        title="Photosynthesis practice",
        subject="Biology",
        grade_level="Grade 10",
        objectives=[LearningObjective(text="Apply the Calvin cycle stoichiometry")],
        warm_up=["Name the two stages."],
        worked_example="6 CO2 + 6 H2O → C6H12O6 + 6 O2",
        sections=[
            WorksheetSection(
                title="Practice",
                instructions="Show your working.",
                answer_lines=3,
                questions=paper.sections[1].questions,
            )
        ],
        challenge=[paper.sections[0].questions[0]],
        reflection_prompt="Which part are you least sure about?",
    )


@pytest.fixture
def rubric() -> Rubric:
    levels = [
        PerformanceLevel(name="Exemplary", weight=1.0),
        PerformanceLevel(name="Proficient", weight=0.75),
        PerformanceLevel(name="Developing", weight=0.5),
    ]
    return Rubric(
        title="Lab report rubric",
        task_description="Write up the photosynthesis experiment.",
        levels=levels,
        criteria=[
            Criterion(
                name="Method",
                max_points=4,
                levels=[
                    CriterionLevel(level_name="Exemplary", descriptor="Fully replicable"),
                    CriterionLevel(level_name="Proficient", descriptor="Mostly replicable"),
                    CriterionLevel(level_name="Developing", descriptor="Key steps missing"),
                ],
            ),
            Criterion(name="Analysis", max_points=6),
        ],
        student_facing_summary="Aim for a method someone else could follow exactly.",
    )


@pytest.fixture
def lesson_plan() -> LessonPlan:
    return LessonPlan(
        title="Introducing photosynthesis",
        subject="Biology",
        grade_level="Grade 10",
        duration_minutes=45,
        objectives=[LearningObjective(text="Describe the inputs and outputs")],
        success_criteria=["I can name the reactants and products"],
        materials=["Projector", "Leaf samples"],
        hook="Show a plant grown in the dark beside one grown in light.",
        activities=[
            Activity(
                name="Starter",
                minutes=5,
                teacher_does="Poses the question",
                students_do="Discuss in pairs",
                check_for_understanding="Thumbs up or down",
            ),
            Activity(
                name="Explanation",
                minutes=20,
                teacher_does="Explains the two stages",
                students_do="Annotate a diagram",
            ),
        ],
        closure="Exit ticket: one thing learned, one question remaining.",
        homework="Complete the worksheet.",
    )


@pytest.fixture
def flashcards() -> FlashcardDeck:
    return FlashcardDeck(
        title="Photosynthesis terms",
        subject="Biology",
        cards=[
            Flashcard(front="Rubisco", back="The enzyme that fixes CO2", tags=["enzymes"]),
            Flashcard(front="Stroma", back="Fluid around the thylakoids", kind=CardKind.REVERSED),
            Flashcard(
                front="Water is split during {{c1::photolysis}}.",
                back="photolysis",
                kind=CardKind.CLOZE,
            ),
        ],
    )


# --------------------------------------------------------------------------- pptx


def test_pptx_renders_every_layout(deck: Deck, tmp_path: Path) -> None:
    out = pptx.render(deck, tmp_path / "deck.pptx")
    assert out.exists()

    from pptx import Presentation

    presentation = Presentation(str(out))
    assert len(presentation.slides) == len(deck.slides)

    # The table slide must actually contain a table, not a bullet fallback.
    table_slide = presentation.slides[3]
    assert any(shape.has_table for shape in table_slide.shapes)

    # Speaker notes survive.
    notes_slide = presentation.slides[1]
    assert "at night" in notes_slide.notes_slide.notes_text_frame.text


@pytest.mark.parametrize("theme", ["academic", "minimal", "chalkboard", "warm", "high_contrast"])
def test_pptx_renders_in_every_theme(deck: Deck, tmp_path: Path, theme: str) -> None:
    deck.theme = theme
    out = pptx.render(deck, tmp_path / f"{theme}.pptx")
    assert out.stat().st_size > 20_000


def test_pptx_long_bullets_shrink_rather_than_overflow() -> None:
    small = pptx._fit_size(["short", "short"], 20)
    crowded = pptx._fit_size(["x" * 120] * 8, 20)
    assert crowded < small
    assert crowded >= 12


def test_markdown_table_parser_skips_the_separator_row() -> None:
    rows = pptx._parse_markdown_table("| A | B |\n|---|:--:|\n| 1 | 2 |")
    assert rows == [["A", "B"], ["1", "2"]]


# --------------------------------------------------------------------------- markdown


def test_deck_markdown_includes_notes_and_slides(deck: Deck) -> None:
    text = markdown.deck_to_markdown(deck)
    assert "# Photosynthesis" in text
    assert "Speaker notes:" in text
    assert text.count("---") >= len(deck.slides)


def test_paper_markdown_hides_answers_from_the_student_copy(paper: QuestionPaper) -> None:
    student = markdown.paper_to_markdown(paper)
    key = markdown.paper_to_markdown(paper, answer_key=True)

    assert "Thylakoid membrane" in student  # it is an option
    assert "✓ **Answer:**" not in student
    assert "✓ **Answer:**" in key
    assert "Mark scheme" in key
    assert "Why the distractors are wrong" in key
    assert "Confuses it with the Calvin cycle" in key


def test_notes_markdown_renders_every_part(notes: LectureNotes) -> None:
    text = markdown.notes_to_markdown(notes)
    for expected in (
        "Learning objectives",
        "Key terms",
        "Worked example",
        "Glossary",
        "Review questions",
        "Further reading",
    ):
        assert expected in text


def test_rubric_markdown_is_a_grid(rubric: Rubric) -> None:
    text = markdown.rubric_to_markdown(rubric)
    assert "| Criterion | Exemplary | Proficient | Developing | Points |" in text
    assert "Fully replicable" in text


def test_lesson_plan_markdown_has_a_timed_sequence(lesson_plan: LessonPlan) -> None:
    text = markdown.lesson_plan_to_markdown(lesson_plan)
    assert "5 min" in text and "20 min" in text
    assert "Checks for understanding" in text


def test_grading_markdown_always_flags_review() -> None:
    result = GradingResult(
        task_title="Lab report",
        total_points=7,
        max_points=10,
        scores=[
            CriterionScore(
                criterion="Method",
                points=3,
                max_points=4,
                justification="Clear but missing a control",
            )
        ],
        strengths=["Good structure"],
        needs_teacher_review=True,
        review_reason="Borderline between two levels",
    )
    text = markdown.grading_to_markdown(result)
    assert "needs a teacher's review" in text
    assert "70%" in text
    assert "AI assistance" in text


# --------------------------------------------------------------------------- docx


def test_docx_paper_and_key_differ(paper: QuestionPaper, tmp_path: Path) -> None:
    from docx import Document

    student = docx.render_paper(paper, tmp_path / "paper.docx")
    key = docx.render_paper(paper, tmp_path / "key.docx", answer_key=True)

    student_text = "\n".join(p.text for p in Document(str(student)).paragraphs)
    key_text = "\n".join(p.text for p in Document(str(key)).paragraphs)

    assert "Name:" in student_text
    assert "Answer:" not in student_text
    assert "Answer:" in key_text
    assert "Mark scheme" in key_text
    assert "Answer Key" in key_text


def test_docx_notes_converts_inline_markdown(notes: LectureNotes, tmp_path: Path) -> None:
    from docx import Document

    out = docx.render_notes(notes, tmp_path / "notes.docx")
    document = Document(str(out))
    text = "\n".join(p.text for p in document.paragraphs)

    # The ** markers must be gone, the word bolded instead.
    assert "**chlorophyll**" not in text
    assert "chlorophyll" in text
    bold_runs = [r.text for p in document.paragraphs for r in p.runs if r.bold]
    assert "chlorophyll" in bold_runs


def test_docx_notes_includes_callouts_as_tables(notes: LectureNotes, tmp_path: Path) -> None:
    from docx import Document

    out = docx.render_notes(notes, tmp_path / "notes.docx")
    document = Document(str(out))
    table_text = "\n".join(c.text for t in document.tables for row in t.rows for c in row.cells)
    assert "Plants respire too" in table_text
    assert "Photolysis" in table_text


def test_docx_worksheet_has_answer_lines(worksheet: Worksheet, tmp_path: Path) -> None:
    from docx import Document

    out = docx.render_worksheet(worksheet, tmp_path / "ws.docx")
    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "_" * 40 in text
    assert "Name:" in text


def test_docx_rubric_is_landscape(rubric: Rubric, tmp_path: Path) -> None:
    from docx import Document

    out = docx.render_rubric(rubric, tmp_path / "rubric.docx")
    section = Document(str(out)).sections[0]
    assert section.page_width > section.page_height


def test_docx_lesson_plan_renders(lesson_plan: LessonPlan, tmp_path: Path) -> None:
    from docx import Document

    out = docx.render_lesson_plan(lesson_plan, tmp_path / "plan.docx")
    document = Document(str(out))
    table_text = "\n".join(c.text for t in document.tables for row in t.rows for c in row.cells)
    assert "Thumbs up or down" in table_text


# --------------------------------------------------------------------------- Moodle XML


def test_moodle_xml_is_well_formed_and_complete(paper: QuestionPaper) -> None:
    xml = lms.to_moodle_xml(paper)
    root = ET.fromstring(xml)

    questions = [q for q in root.findall("question") if q.get("type") != "category"]
    assert len(questions) == len(paper.questions)

    types = {q.get("type") for q in questions}
    assert {"multichoice", "truefalse", "shortanswer", "numerical", "matching", "essay"} <= types


def test_moodle_mcq_fractions_sum_to_100(paper: QuestionPaper) -> None:
    root = ET.fromstring(lms.to_moodle_xml(paper))
    for question in root.findall("question"):
        if question.get("type") != "multichoice":
            continue
        positive = [
            float(a.get("fraction"))
            for a in question.findall("answer")
            if float(a.get("fraction")) > 0
        ]
        assert sum(positive) == pytest.approx(100.0)


def test_moodle_escapes_dangerous_content() -> None:
    question = Question(
        type=QuestionType.SHORT_ANSWER,
        text='Explain <script>alert("x")</script> & the "ampersand" rule.',
        answer="It must survive ]]> even here.",
    )
    paper = QuestionPaper(sections=[PaperSection(title="A", questions=[question])])
    xml = lms.to_moodle_xml(paper)

    root = ET.fromstring(xml)  # must parse
    # The script tag is inert text, not live markup, and the answer round-trips intact.
    stem = root.find(".//questiontext/text").text
    assert "<script>" not in stem
    assert "&lt;script&gt;" in stem
    assert root.find(".//answer/text").text == "It must survive ]]> even here."


def test_cdata_neutralises_an_embedded_terminator() -> None:
    """A stem containing ]]> would otherwise close the CDATA section early."""
    wrapped = lms._cdata("before ]]> after")
    assert wrapped == "<![CDATA[before ]]]]><![CDATA[> after]]>"
    ET.fromstring(f"<t>{wrapped}</t>")


def test_moodle_truefalse_has_both_options(paper: QuestionPaper) -> None:
    root = ET.fromstring(lms.to_moodle_xml(paper))
    tf = next(q for q in root.findall("question") if q.get("type") == "truefalse")
    answers = {a.find("text").text: float(a.get("fraction")) for a in tf.findall("answer")}
    assert answers == {"true": 0.0, "false": 100.0}


# --------------------------------------------------------------------------- GIFT


def test_gift_contains_every_question(paper: QuestionPaper) -> None:
    gift = lms.to_gift(paper)
    assert gift.count("::") >= len(paper.questions) * 2  # each entry opens and closes a title
    # The true/false item renders its marker immediately after the opening brace; the
    # feedback comment follows it on the next line.
    assert "{F\n" in gift
    assert "#1:0.01" in gift  # the numerical question
    assert " -> " in gift  # the matching question


def test_gift_escapes_reserved_characters() -> None:
    question = Question(
        type=QuestionType.SHORT_ANSWER,
        text="What does {x = y} mean? Use ~ and #.",
        answer="equality",
    )
    paper = QuestionPaper(sections=[PaperSection(title="A", questions=[question])])
    gift = lms.to_gift(paper)
    assert "\\{" in gift and "\\}" in gift and "\\~" in gift and "\\#" in gift


# --------------------------------------------------------------------------- QTI


def test_qti_package_structure(paper: QuestionPaper, tmp_path: Path) -> None:
    out = lms.to_qti_package(paper, tmp_path / "quiz.zip")
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert "imsmanifest.xml" in names
        assert "assessment.xml" in names
        assert len([n for n in names if n.startswith("items/")]) == len(paper.questions)

        # Everything must be valid XML.
        for name in names:
            ET.fromstring(archive.read(name))


def test_qti_multi_select_allows_multiple_choices(paper: QuestionPaper, tmp_path: Path) -> None:
    out = lms.to_qti_package(paper, tmp_path / "quiz.zip")
    with zipfile.ZipFile(out) as archive:
        items = [archive.read(n).decode() for n in archive.namelist() if n.startswith("items/")]
    multi = [i for i in items if 'cardinality="multiple"' in i]
    assert len(multi) == 1
    assert 'maxChoices="0"' in multi[0]


# --------------------------------------------------------------------------- CSV


def test_csv_has_a_row_per_question(paper: QuestionPaper) -> None:
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(lms.to_csv(paper))))
    assert len(rows) == len(paper.questions)
    assert rows[0]["type"] == "mcq"
    assert rows[0]["correct"] == "Thylakoid membrane"


def test_google_forms_csv_skips_unsupported_types(paper: QuestionPaper) -> None:
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(lms.to_google_forms_csv(paper))))
    kinds = {r["Question Type"] for r in rows}
    assert kinds <= {"Multiple choice", "Checkbox", "Short answer", "Paragraph"}
    assert "matching" in lms.unsupported_in_forms(paper)
    assert "case_study" in lms.unsupported_in_forms(paper)


# --------------------------------------------------------------------------- Anki


def test_anki_package_is_written(flashcards: FlashcardDeck, tmp_path: Path) -> None:
    out = anki.render(flashcards, tmp_path / "deck.apkg")
    assert out.exists()
    assert out.stat().st_size > 1000
    with zipfile.ZipFile(out) as archive:
        assert any(n.startswith("collection") for n in archive.namelist())


def test_anki_ids_are_stable_across_exports(flashcards: FlashcardDeck, tmp_path: Path) -> None:
    """Re-exporting must update the teacher's deck, not create a second one."""
    first = anki._stable_id("deck", flashcards.title)
    second = anki._stable_id("deck", flashcards.title)
    assert first == second
    assert first != anki._stable_id("deck", "A different deck")


def test_auto_cloze_wraps_the_answer() -> None:
    assert anki._auto_cloze("Water is split during photolysis.", "photolysis") == (
        "Water is split during {{c1::photolysis}}."
    )


def test_quizlet_tsv_is_two_columns(flashcards: FlashcardDeck) -> None:
    lines = [line for line in anki.to_quizlet_tsv(flashcards).splitlines() if line]
    assert len(lines) == len(flashcards.cards)
    assert all(line.count("\t") == 1 for line in lines)
