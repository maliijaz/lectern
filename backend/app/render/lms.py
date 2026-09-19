"""Export question papers to learning-management-system formats.

These are hand-written rather than pulled from a library because the formats are small,
stable and well documented, and because every library in this space either targets one
platform or drags in a framework. Writing them directly also means each question type maps
deliberately: a `case_study` has no native equivalent anywhere, so the choice of how to
degrade it is ours to make rather than a library's to guess.

Supported:
* **Moodle XML** — the richest target; imports into a Moodle question bank.
* **GIFT** — Moodle's plain-text format; also read by several other tools.
* **QTI 2.1** — an IMS content package (zip), for Canvas, Blackboard, D2L and OpenEdX.
* **CSV** — for Google Forms / Quizlet style importers and for spreadsheets.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from app.core.logging import get_logger
from app.schemas.paper import Question, QuestionPaper, QuestionType

log = get_logger(__name__)

#: Types with no native representation in a given format degrade to an essay/free-text
#: question rather than being dropped — a teacher would rather edit one than lose it.
_ESSAY_FALLBACK = {QuestionType.CASE_STUDY, QuestionType.DIAGRAM_LABEL, QuestionType.LONG_ANSWER}


def _cdata(text: str) -> str:
    """Wrap text as CDATA, neutralising any literal ``]]>`` inside it."""
    return f"<![CDATA[{text.replace(']]>', ']]]]><![CDATA[>')}]]>"


def _html(text: str) -> str:
    """Model text is plain with newlines; LMS fields expect HTML."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{escape(line).replace(chr(10), '<br/>')}</p>" for line in paragraphs)


def _question_title(question: Question, index: int) -> str:
    stem = question.text.strip().replace("\n", " ")
    return f"{index:03d} {stem[:60]}" + ("…" if len(stem) > 60 else "")


# --------------------------------------------------------------------------- Moodle XML


def to_moodle_xml(paper: QuestionPaper, *, category: str = "") -> str:
    """A Moodle question-bank XML document."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<quiz>"]

    category = category or (paper.meta.exam_name or "Imported questions")
    lines += [
        '  <question type="category">',
        "    <category>",
        f"      <text>$course$/top/{escape(category)}</text>",
        "    </category>",
        "  </question>",
    ]

    for index, question in enumerate(paper.questions, start=1):
        block = _moodle_question(question, index)
        if block:
            lines.append(block)

    lines.append("</quiz>")
    return "\n".join(lines) + "\n"


def _moodle_question(question: Question, index: int) -> str:
    moodle_type = {
        QuestionType.MCQ: "multichoice",
        QuestionType.MULTI_SELECT: "multichoice",
        QuestionType.ASSERTION_REASON: "multichoice",
        QuestionType.TRUE_FALSE: "truefalse",
        QuestionType.SHORT_ANSWER: "shortanswer",
        QuestionType.FILL_BLANK: "shortanswer",
        QuestionType.NUMERICAL: "numerical",
        QuestionType.MATCHING: "matching",
    }.get(question.type, "essay")

    stem = question.text
    if question.scenario:
        stem = f"{question.scenario}\n\n{stem}"
    if question.sub_questions:
        stem += "\n\n" + "\n".join(
            f"({s.label or i + 1}) {s.text} [{s.marks:g}]"
            for i, s in enumerate(question.sub_questions)
        )

    head = [
        f'  <question type="{moodle_type}">',
        "    <name>",
        f"      <text>{escape(_question_title(question, index))}</text>",
        "    </name>",
        '    <questiontext format="html">',
        f"      <text>{_cdata(_html(stem))}</text>",
        "    </questiontext>",
    ]
    if question.explanation:
        head += [
            '    <generalfeedback format="html">',
            f"      <text>{_cdata(_html(question.explanation))}</text>",
            "    </generalfeedback>",
        ]
    head += [
        f"    <defaultgrade>{question.marks:g}</defaultgrade>",
        "    <penalty>0.3333333</penalty>",
        "    <hidden>0</hidden>",
        f"    <tags><tag><text>{escape(question.topic or 'general')}</text></tag>"
        f"<tag><text>{question.bloom.value}</text></tag>"
        f"<tag><text>{question.difficulty.value}</text></tag></tags>",
    ]

    body = _moodle_body(question, moodle_type)
    return "\n".join(head + body + ["  </question>"])


def _moodle_body(question: Question, moodle_type: str) -> list[str]:
    if moodle_type == "multichoice":
        correct = question.correct_options()
        single = "true" if len(correct) == 1 else "false"
        # Moodle expects fractions to sum to 100 across correct answers.
        share = 100.0 / len(correct) if correct else 0.0
        lines = [
            f"    <single>{single}</single>",
            "    <shuffleanswers>1</shuffleanswers>",
            "    <answernumbering>ABCD</answernumbering>",
        ]
        wrong_count = len(question.options) - len(correct)
        penalty = -100.0 / wrong_count if wrong_count and single == "false" else 0.0
        for option in question.options:
            fraction = share if option.is_correct else penalty
            lines += [
                f'    <answer fraction="{fraction:g}" format="html">',
                f"      <text>{_cdata(_html(option.text))}</text>",
                '      <feedback format="html">',
                f"        <text>{_cdata(_html(option.rationale))}</text>",
                "      </feedback>",
                "    </answer>",
            ]
        return lines

    if moodle_type == "truefalse":
        lines = []
        for value in (True, False):
            fraction = 100 if value == question.correct_bool else 0
            lines += [
                f'    <answer fraction="{fraction}" format="moodle_auto_format">',
                f"      <text>{'true' if value else 'false'}</text>",
                '      <feedback format="html">',
                f"        <text>{_cdata(_html(question.explanation))}</text>",
                "      </feedback>",
                "    </answer>",
            ]
        return lines

    if moodle_type == "shortanswer":
        accepted = question.blanks or ([question.answer] if question.answer else [])
        accepted += [k for k in question.keywords if k not in accepted]
        lines = ["    <usecase>0</usecase>"]
        for value in accepted:
            lines += [
                '    <answer fraction="100" format="moodle_auto_format">',
                f"      <text>{escape(value)}</text>",
                "    </answer>",
            ]
        return lines

    if moodle_type == "numerical":
        return [
            '    <answer fraction="100" format="moodle_auto_format">',
            f"      <text>{question.numeric_answer:g}</text>",
            f"      <tolerance>{question.tolerance:g}</tolerance>",
            "    </answer>",
            f"    <units><unit><multiplier>1</multiplier>"
            f"<unit_name>{escape(question.unit)}</unit_name></unit></units>"
            if question.unit
            else "",
        ]

    if moodle_type == "matching":
        lines = ["    <shuffleanswers>1</shuffleanswers>"]
        for pair in question.pairs:
            lines += [
                '    <subquestion format="html">',
                f"      <text>{_cdata(_html(pair.left))}</text>",
                "      <answer>",
                f"        <text>{escape(pair.right)}</text>",
                "      </answer>",
                "    </subquestion>",
            ]
        return lines

    # essay
    lines = [
        "    <responseformat>editor</responseformat>",
        "    <responserequired>1</responserequired>",
        "    <responsefieldlines>15</responsefieldlines>",
        "    <attachments>0</attachments>",
    ]
    scheme = question.answer or "\n".join(
        f"- {p.description} ({p.marks:g})" for p in question.rubric_points
    )
    if scheme:
        lines += [
            '    <graderinfo format="html">',
            f"      <text>{_cdata(_html(scheme))}</text>",
            "    </graderinfo>",
        ]
    return lines


# --------------------------------------------------------------------------- GIFT


def to_gift(paper: QuestionPaper) -> str:
    """Moodle's plain-text GIFT format. Compact, human-editable, widely supported."""
    blocks = [f"// {paper.meta.exam_name or 'Question bank'}", ""]

    for section in paper.sections:
        blocks.append(f"$CATEGORY: $course$/top/{section.title}")
        blocks.append("")
        for index, question in enumerate(section.questions, start=1):
            block = _gift_question(question, index)
            if block:
                blocks += [block, ""]

    return "\n".join(blocks)


def _gift_escape(text: str) -> str:
    """GIFT reserves ``~ = # { } :`` and treats a newline as a separator."""
    out = text.replace("\\", "\\\\")
    for char in "~=#{}:":
        out = out.replace(char, f"\\{char}")
    return out.replace("\n", " ")


def _gift_question(question: Question, index: int) -> str:
    title = _gift_escape(_question_title(question, index))
    stem = _gift_escape(
        f"{question.scenario}\n\n{question.text}" if question.scenario else question.text
    )
    head = f"::{title}::{stem}"
    feedback = f"\n#### {_gift_escape(question.explanation)}" if question.explanation else ""

    match question.type:
        case QuestionType.MCQ | QuestionType.ASSERTION_REASON:
            options = "\n".join(
                f"  {'=' if o.is_correct else '~'}{_gift_escape(o.text)}"
                + (f" # {_gift_escape(o.rationale)}" if o.rationale else "")
                for o in question.options
            )
            return f"{head} {{\n{options}{feedback}\n}}"

        case QuestionType.MULTI_SELECT:
            correct = question.correct_options()
            weight = 100 / len(correct) if correct else 0
            wrong = len(question.options) - len(correct)
            penalty = -100 / wrong if wrong else 0
            options = "\n".join(
                f"  ~%{(weight if o.is_correct else penalty):g}%{_gift_escape(o.text)}"
                for o in question.options
            )
            return f"{head} {{\n{options}{feedback}\n}}"

        case QuestionType.TRUE_FALSE:
            return f"{head} {{{'T' if question.correct_bool else 'F'}{feedback}\n}}"

        case QuestionType.SHORT_ANSWER | QuestionType.FILL_BLANK:
            accepted = question.blanks or ([question.answer] if question.answer else [])
            answers = "\n".join(f"  ={_gift_escape(a)}" for a in accepted if a)
            return f"{head} {{\n{answers}{feedback}\n}}"

        case QuestionType.NUMERICAL:
            return f"{head} {{#{question.numeric_answer:g}:{question.tolerance:g}{feedback}\n}}"

        case QuestionType.MATCHING:
            pairs = "\n".join(
                f"  ={_gift_escape(p.left)} -> {_gift_escape(p.right)}" for p in question.pairs
            )
            return f"{head} {{\n{pairs}\n}}"

        case _:
            # Essay. GIFT has no mark scheme field, so it goes in a comment.
            scheme = question.answer or "; ".join(p.description for p in question.rubric_points)
            comment = f"\n// Mark scheme: {scheme}" if scheme else ""
            return f"{head} {{}}{comment}"


# --------------------------------------------------------------------------- QTI 2.1


def to_qti_package(paper: QuestionPaper, out_path: Path) -> Path:
    """Write an IMS QTI 2.1 content package (a .zip) for Canvas, Blackboard and D2L."""
    items: list[tuple[str, str]] = []
    for index, question in enumerate(paper.questions, start=1):
        identifier = f"item_{index:04d}"
        items.append((identifier, _qti_item(question, identifier, index)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("imsmanifest.xml", _qti_manifest(paper, items))
        archive.writestr("assessment.xml", _qti_test(paper, items))
        for identifier, xml in items:
            archive.writestr(f"items/{identifier}.xml", xml)

    log.info("Wrote QTI package with %d items to %s", len(items), out_path.name)
    return out_path


def _qti_item(question: Question, identifier: str, index: int) -> str:
    stem = _html(f"{question.scenario}\n\n{question.text}" if question.scenario else question.text)
    title = quoteattr(_question_title(question, index))

    if question.type in (
        QuestionType.MCQ,
        QuestionType.MULTI_SELECT,
        QuestionType.ASSERTION_REASON,
        QuestionType.TRUE_FALSE,
    ):
        return _qti_choice_item(question, identifier, title, stem)
    if question.type == QuestionType.NUMERICAL:
        return _qti_text_entry_item(
            identifier, title, stem, [f"{question.numeric_answer:g}"], numeric=True
        )
    if question.type in (QuestionType.SHORT_ANSWER, QuestionType.FILL_BLANK):
        accepted = question.blanks or ([question.answer] if question.answer else [])
        return _qti_text_entry_item(identifier, title, stem, accepted)
    return _qti_extended_text_item(question, identifier, title, stem)


def _qti_choice_item(question: Question, identifier: str, title: str, stem: str) -> str:
    if question.type == QuestionType.TRUE_FALSE:
        options = [
            ("true", "True", bool(question.correct_bool)),
            ("false", "False", not question.correct_bool),
        ]
    else:
        options = [
            (f"choice_{i}", option.text, option.is_correct)
            for i, option in enumerate(question.options)
        ]

    cardinality = "multiple" if question.type == QuestionType.MULTI_SELECT else "single"
    max_choices = 0 if cardinality == "multiple" else 1
    correct = "".join(
        f"\n      <value>{value}</value>" for value, _, is_correct in options if is_correct
    )
    choices = "".join(
        f'\n    <simpleChoice identifier="{value}">{escape(text)}</simpleChoice>'
        for value, text, _ in options
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
  identifier="{identifier}" title={title} adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE" cardinality="{cardinality}" baseType="identifier">
    <correctResponse>{correct}
    </correctResponse>
  </responseDeclaration>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">
    <defaultValue><value>0</value></defaultValue>
  </outcomeDeclaration>
  <itemBody>
    {stem}
    <choiceInteraction responseIdentifier="RESPONSE" shuffle="true" maxChoices="{max_choices}">{choices}
    </choiceInteraction>
  </itemBody>
  <responseProcessing template="http://www.imsglobal.org/question/qti_v2p1/rptemplates/match_correct"/>
</assessmentItem>
"""


def _qti_text_entry_item(
    identifier: str, title: str, stem: str, accepted: list[str], *, numeric: bool = False
) -> str:
    base_type = "float" if numeric else "string"
    values = "".join(f"\n      <value>{escape(v)}</value>" for v in accepted if v)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
  identifier="{identifier}" title={title} adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE" cardinality="single" baseType="{base_type}">
    <correctResponse>{values}
    </correctResponse>
  </responseDeclaration>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">
    <defaultValue><value>0</value></defaultValue>
  </outcomeDeclaration>
  <itemBody>
    {stem}
    <textEntryInteraction responseIdentifier="RESPONSE" expectedLength="40"/>
  </itemBody>
  <responseProcessing template="http://www.imsglobal.org/question/qti_v2p1/rptemplates/map_response"/>
</assessmentItem>
"""


def _qti_extended_text_item(question: Question, identifier: str, title: str, stem: str) -> str:
    scheme = question.answer or "\n".join(
        f"{p.description} ({p.marks:g})" for p in question.rubric_points
    )
    rubric = f'\n    <rubricBlock view="scorer">{_html(scheme)}</rubricBlock>' if scheme else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
  identifier="{identifier}" title={title} adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE" cardinality="single" baseType="string"/>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float">
    <defaultValue><value>0</value></defaultValue>
  </outcomeDeclaration>
  <itemBody>
    {stem}{rubric}
    <extendedTextInteraction responseIdentifier="RESPONSE" expectedLines="12"/>
  </itemBody>
</assessmentItem>
"""


def _qti_manifest(paper: QuestionPaper, items: list[tuple[str, str]]) -> str:
    resources = "\n".join(
        f'    <resource identifier="{identifier}" type="imsqti_item_xmlv2p1" '
        f'href="items/{identifier}.xml">\n'
        f'      <file href="items/{identifier}.xml"/>\n'
        f"    </resource>"
        for identifier, _ in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
  xmlns:imsmd="http://www.imsglobal.org/xsd/imsmd_v1p2"
  identifier="manifest_{abs(hash(paper.meta.exam_name)) % 10**8}">
  <metadata>
    <schema>IMS Content</schema>
    <schemaversion>1.1.3</schemaversion>
  </metadata>
  <organizations/>
  <resources>
    <resource identifier="assessment" type="imsqti_test_xmlv2p1" href="assessment.xml">
      <file href="assessment.xml"/>
    </resource>
{resources}
  </resources>
</manifest>
"""


def _qti_test(paper: QuestionPaper, items: list[tuple[str, str]]) -> str:
    refs = "\n".join(
        f'      <assessmentItemRef identifier="ref_{identifier}" href="items/{identifier}.xml"/>'
        for identifier, _ in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<assessmentTest xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
  identifier="assessment" title={quoteattr(paper.meta.exam_name or "Examination")}>
  <testPart identifier="part1" navigationMode="nonlinear" submissionMode="individual">
    <assessmentSection identifier="section1" title="Questions" visible="true">
{refs}
    </assessmentSection>
  </testPart>
</assessmentTest>
"""


# --------------------------------------------------------------------------- CSV


def to_csv(paper: QuestionPaper) -> str:
    """A flat CSV: one row per question, options in their own columns.

    The shape most quiz importers and spreadsheet workflows expect, and the easiest thing
    for a teacher to eyeball before importing anywhere.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    max_options = max((len(q.options) for q in paper.questions), default=0)
    header = [
        "number",
        "section",
        "type",
        "question",
        "marks",
        "topic",
        "bloom",
        "difficulty",
        *[f"option_{chr(ord('A') + i)}" for i in range(max_options)],
        "correct",
        "explanation",
    ]
    writer.writerow(header)

    number = 0
    for section in paper.sections:
        for question in section.questions:
            number += 1
            options = [o.text for o in question.options]
            options += [""] * (max_options - len(options))
            writer.writerow(
                [
                    number,
                    section.title,
                    question.type.value,
                    question.text,
                    f"{question.marks:g}",
                    question.topic,
                    question.bloom.value,
                    question.difficulty.value,
                    *options,
                    question.answer_text(),
                    question.explanation,
                ]
            )

    return buffer.getvalue()


def to_google_forms_csv(paper: QuestionPaper) -> str:
    """The narrower shape Google Forms importer add-ons expect.

    Only question types Forms can represent are included; the rest are reported so the
    caller can tell the teacher what did not make it across.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "Question",
            "Question Type",
            "Option 1",
            "Option 2",
            "Option 3",
            "Option 4",
            "Option 5",
            "Correct Answer",
            "Points",
            "Feedback",
        ]
    )

    forms_type = {
        QuestionType.MCQ: "Multiple choice",
        QuestionType.ASSERTION_REASON: "Multiple choice",
        QuestionType.MULTI_SELECT: "Checkbox",
        QuestionType.TRUE_FALSE: "Multiple choice",
        QuestionType.SHORT_ANSWER: "Short answer",
        QuestionType.FILL_BLANK: "Short answer",
        QuestionType.NUMERICAL: "Short answer",
        QuestionType.LONG_ANSWER: "Paragraph",
    }

    for question in paper.questions:
        kind = forms_type.get(question.type)
        if kind is None:
            continue
        if question.type == QuestionType.TRUE_FALSE:
            options = ["True", "False", "", "", ""]
        else:
            options = [o.text for o in question.options[:5]]
            options += [""] * (5 - len(options))
        writer.writerow(
            [
                question.text,
                kind,
                *options,
                question.answer_text(),
                f"{question.marks:g}",
                question.explanation,
            ]
        )

    return buffer.getvalue()


def unsupported_in_forms(paper: QuestionPaper) -> list[str]:
    """Question types Google Forms cannot represent, for an honest warning in the UI."""
    unsupported = {QuestionType.MATCHING, QuestionType.CASE_STUDY, QuestionType.DIAGRAM_LABEL}
    return sorted({q.type.value for q in paper.questions if q.type in unsupported})
