/** Structured editors.
 *
 * These are the reason the product stores validated JSON rather than files: a teacher can
 * fix the one bullet that is wrong, or swap a distractor, and re-render every format from
 * the corrected content without paying for another generation.
 *
 * Each editor edits a deep copy and reports changes upward; the page owns saving.
 */

import { useState } from "react";
import { Card, Field, Toggle } from "./ui";

type Setter = (next: any) => void;

function ListEditor({
  items,
  onChange,
  placeholder,
  multiline = false,
}: {
  items: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  const update = (index: number, value: string) =>
    onChange(items.map((item, i) => (i === index ? value : item)));

  return (
    <div className="space-y-1.5">
      {items.map((item, index) => (
        <div key={index} className="flex gap-1.5 items-start">
          <span className="text-xs text-[color:var(--color-ink-faint)] pt-2 w-4 shrink-0 tabular-nums">
            {index + 1}
          </span>
          {multiline ? (
            <textarea
              className="field min-h-16"
              value={item}
              placeholder={placeholder}
              onChange={(event) => update(index, event.target.value)}
            />
          ) : (
            <input
              className="field"
              value={item}
              placeholder={placeholder}
              onChange={(event) => update(index, event.target.value)}
            />
          )}
          <button
            type="button"
            className="btn !px-2 !py-1.5 shrink-0"
            aria-label={`Remove item ${index + 1}`}
            onClick={() => onChange(items.filter((_, i) => i !== index))}
          >
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="btn !py-1 text-xs" onClick={() => onChange([...items, ""])}>
        + Add
      </button>
    </div>
  );
}

function Collapsible({
  title,
  badge,
  children,
  defaultOpen = false,
}: {
  title: string;
  badge?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card">
      <button
        type="button"
        className="w-full flex items-center gap-2 px-3.5 py-2.5 text-left"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span
          className="text-xs text-[color:var(--color-ink-faint)] transition-transform"
          style={{ transform: open ? "rotate(90deg)" : "none" }}
          aria-hidden="true"
        >
          ▶
        </span>
        <span className="font-medium text-sm flex-1 truncate">{title}</span>
        {badge && <span className="chip">{badge}</span>}
      </button>
      {open && (
        <div className="px-3.5 pb-3.5 pt-1 border-t border-[color:var(--color-line)] space-y-3">
          {children}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- slides

export function DeckEditor({ content, onChange }: { content: any; onChange: Setter }) {
  const slides: any[] = content.slides ?? [];

  const updateSlide = (index: number, patch: Record<string, unknown>) =>
    onChange({
      ...content,
      slides: slides.map((slide, i) => (i === index ? { ...slide, ...patch } : slide)),
    });

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= slides.length) return;
    const next = [...slides];
    [next[index], next[target]] = [next[target], next[index]];
    onChange({ ...content, slides: next });
  };

  return (
    <div className="space-y-3">
      <Card className="space-y-3">
        <Field label="Deck title">
          <input
            className="field"
            value={content.title ?? ""}
            onChange={(event) => onChange({ ...content, title: event.target.value })}
          />
        </Field>
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Subtitle">
            <input
              className="field"
              value={content.subtitle ?? ""}
              onChange={(event) => onChange({ ...content, subtitle: event.target.value })}
            />
          </Field>
          <Field label="Presenter">
            <input
              className="field"
              value={content.presenter ?? ""}
              onChange={(event) => onChange({ ...content, presenter: event.target.value })}
            />
          </Field>
        </div>
      </Card>

      {slides.map((slide, index) => (
        <Collapsible
          key={index}
          title={`${index + 1}. ${slide.heading || slide.layout}`}
          badge={slide.layout}
        >
          <div className="flex gap-1.5">
            <button type="button" className="btn !py-1 !px-2 text-xs" onClick={() => move(index, -1)}>
              ↑
            </button>
            <button type="button" className="btn !py-1 !px-2 text-xs" onClick={() => move(index, 1)}>
              ↓
            </button>
            <button
              type="button"
              className="btn btn-danger !py-1 !px-2 text-xs ml-auto"
              onClick={() => onChange({ ...content, slides: slides.filter((_, i) => i !== index) })}
            >
              Delete slide
            </button>
          </div>

          <Field label="Heading">
            <input
              className="field"
              value={slide.heading ?? ""}
              onChange={(event) => updateSlide(index, { heading: event.target.value })}
            />
          </Field>

          {(slide.bullets ?? []).length > 0 || slide.layout === "bullets" ? (
            <div>
              <span className="label">Bullets</span>
              <ListEditor
                items={slide.bullets ?? []}
                onChange={(bullets) => updateSlide(index, { bullets })}
                placeholder="A short phrase, not a sentence"
              />
            </div>
          ) : null}

          {slide.quote !== undefined && slide.layout === "quote" && (
            <Field label="Quote">
              <textarea
                className="field min-h-20"
                value={slide.quote ?? ""}
                onChange={(event) => updateSlide(index, { quote: event.target.value })}
              />
            </Field>
          )}

          {(slide.columns ?? []).map((column: any, columnIndex: number) => (
            <div key={columnIndex} className="border-l-2 pl-3" style={{ borderColor: "var(--color-line)" }}>
              <Field label={`Column ${columnIndex + 1} heading`}>
                <input
                  className="field"
                  value={column.heading ?? ""}
                  onChange={(event) =>
                    updateSlide(index, {
                      columns: slide.columns.map((c: any, i: number) =>
                        i === columnIndex ? { ...c, heading: event.target.value } : c,
                      ),
                    })
                  }
                />
              </Field>
              <div className="mt-2">
                <span className="label">Points</span>
                <ListEditor
                  items={column.bullets ?? []}
                  onChange={(bullets) =>
                    updateSlide(index, {
                      columns: slide.columns.map((c: any, i: number) =>
                        i === columnIndex ? { ...c, bullets } : c,
                      ),
                    })
                  }
                />
              </div>
            </div>
          ))}

          <Field label="Speaker notes" hint="What you actually say. Not shown on the slide.">
            <textarea
              className="field min-h-24"
              value={slide.speaker_notes ?? ""}
              onChange={(event) => updateSlide(index, { speaker_notes: event.target.value })}
            />
          </Field>

          {(slide.citations ?? []).length > 0 && (
            <p className="text-xs text-[color:var(--color-ink-faint)]">
              Sources: {slide.citations.map((c: any) => c.document_title || c.document_id).join("; ")}
            </p>
          )}
        </Collapsible>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- notes

export function NotesEditor({ content, onChange }: { content: any; onChange: Setter }) {
  const sections: any[] = content.sections ?? [];

  const updateSection = (index: number, patch: Record<string, unknown>) =>
    onChange({
      ...content,
      sections: sections.map((section, i) => (i === index ? { ...section, ...patch } : section)),
    });

  return (
    <div className="space-y-3">
      <Card className="space-y-3">
        <Field label="Title">
          <input
            className="field"
            value={content.title ?? ""}
            onChange={(event) => onChange({ ...content, title: event.target.value })}
          />
        </Field>
        <Field label="Overview">
          <textarea
            className="field min-h-24"
            value={content.overview ?? ""}
            onChange={(event) => onChange({ ...content, overview: event.target.value })}
          />
        </Field>
      </Card>

      {sections.map((section, index) => (
        <Collapsible key={index} title={section.heading || `Section ${index + 1}`}>
          <Field label="Heading">
            <input
              className="field"
              value={section.heading ?? ""}
              onChange={(event) => updateSection(index, { heading: event.target.value })}
            />
          </Field>
          <Field label="Body" hint="Markdown: **bold**, lists, paragraphs.">
            <textarea
              className="field min-h-56 font-mono text-[13px]"
              value={section.body ?? ""}
              onChange={(event) => updateSection(index, { body: event.target.value })}
            />
          </Field>

          {(section.key_terms ?? []).length > 0 && (
            <div>
              <span className="label">Key terms</span>
              <div className="space-y-1.5">
                {section.key_terms.map((term: any, termIndex: number) => (
                  <div key={termIndex} className="grid grid-cols-[1fr_2fr] gap-1.5">
                    <input
                      className="field"
                      value={term.term ?? ""}
                      onChange={(event) =>
                        updateSection(index, {
                          key_terms: section.key_terms.map((t: any, i: number) =>
                            i === termIndex ? { ...t, term: event.target.value } : t,
                          ),
                        })
                      }
                    />
                    <input
                      className="field"
                      value={term.definition ?? ""}
                      onChange={(event) =>
                        updateSection(index, {
                          key_terms: section.key_terms.map((t: any, i: number) =>
                            i === termIndex ? { ...t, definition: event.target.value } : t,
                          ),
                        })
                      }
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            type="button"
            className="btn btn-danger !py-1 text-xs"
            onClick={() =>
              onChange({ ...content, sections: sections.filter((_, i) => i !== index) })
            }
          >
            Delete section
          </button>
        </Collapsible>
      ))}

      <Card className="space-y-3">
        <Field label="Summary">
          <textarea
            className="field min-h-24"
            value={content.summary ?? ""}
            onChange={(event) => onChange({ ...content, summary: event.target.value })}
          />
        </Field>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- papers

const BLOOM = ["remember", "understand", "apply", "analyze", "evaluate", "create"];
const DIFFICULTY = ["easy", "medium", "hard"];

export function PaperEditor({ content, onChange }: { content: any; onChange: Setter }) {
  const sections: any[] = content.sections ?? [];

  const updateQuestion = (
    sectionIndex: number,
    questionIndex: number,
    patch: Record<string, unknown>,
  ) =>
    onChange({
      ...content,
      sections: sections.map((section, s) =>
        s !== sectionIndex
          ? section
          : {
              ...section,
              questions: section.questions.map((question: any, q: number) =>
                q === questionIndex ? { ...question, ...patch } : question,
              ),
            },
      ),
    });

  const totalMarks = sections.reduce(
    (sum, section) =>
      sum + (section.questions ?? []).reduce((s: number, q: any) => s + (q.marks ?? 0), 0),
    0,
  );

  let number = 0;

  return (
    <div className="space-y-3">
      <Card className="space-y-3">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Exam name">
            <input
              className="field"
              value={content.meta?.exam_name ?? ""}
              onChange={(event) =>
                onChange({ ...content, meta: { ...content.meta, exam_name: event.target.value } })
              }
            />
          </Field>
          <Field label="School or university">
            <input
              className="field"
              value={content.meta?.institution ?? ""}
              onChange={(event) =>
                onChange({ ...content, meta: { ...content.meta, institution: event.target.value } })
              }
            />
          </Field>
        </div>
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="Course">
            <input
              className="field"
              value={content.meta?.course ?? ""}
              onChange={(event) =>
                onChange({ ...content, meta: { ...content.meta, course: event.target.value } })
              }
            />
          </Field>
          <Field label="Duration" hint="Minutes.">
            <input
              type="number"
              className="field"
              value={content.meta?.duration_minutes ?? 60}
              onChange={(event) =>
                onChange({
                  ...content,
                  meta: { ...content.meta, duration_minutes: Number(event.target.value) },
                })
              }
            />
          </Field>
          <Field label="Date">
            <input
              className="field"
              value={content.meta?.date ?? ""}
              onChange={(event) =>
                onChange({ ...content, meta: { ...content.meta, date: event.target.value } })
              }
            />
          </Field>
        </div>
        <p className="text-sm">
          <strong className="tabular-nums">{totalMarks}</strong> marks across{" "}
          {sections.reduce((n, s) => n + (s.questions?.length ?? 0), 0)} questions
        </p>
      </Card>

      {sections.map((section, sectionIndex) => (
        <div key={sectionIndex} className="space-y-2">
          <h3 className="font-semibold text-sm pt-2">{section.title}</h3>
          {(section.questions ?? []).map((question: any, questionIndex: number) => {
            number += 1;
            return (
              <Collapsible
                key={questionIndex}
                title={`${question.needs_review ? "⚠ " : ""}${number}. ${question.text?.slice(0, 70) ?? ""}`}
                badge={`${question.marks ?? 0} mark${question.marks === 1 ? "" : "s"}`}
                defaultOpen={!!question.needs_review}
              >
                {question.needs_review && (
                  <div
                    className="rounded-md px-3 py-2 text-sm"
                    style={{
                      background: "var(--color-danger-soft)",
                      color: "var(--color-danger)",
                    }}
                  >
                    <strong>Check this answer.</strong>{" "}
                    <span className="text-[color:var(--color-ink-soft)]">
                      {question.review_note}
                    </span>
                    <button
                      type="button"
                      className="btn !py-0.5 !px-2 text-xs ml-2"
                      onClick={() =>
                        updateQuestion(sectionIndex, questionIndex, {
                          needs_review: false,
                          review_note: "",
                        })
                      }
                    >
                      I have checked it
                    </button>
                  </div>
                )}

                <Field label="Question">
                  <textarea
                    className="field min-h-20"
                    value={question.text ?? ""}
                    onChange={(event) =>
                      updateQuestion(sectionIndex, questionIndex, { text: event.target.value })
                    }
                  />
                </Field>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <Field label="Marks">
                    <input
                      type="number"
                      step={0.5}
                      className="field"
                      value={question.marks ?? 1}
                      onChange={(event) =>
                        updateQuestion(sectionIndex, questionIndex, {
                          marks: Number(event.target.value),
                        })
                      }
                    />
                  </Field>
                  <Field label="Topic">
                    <input
                      className="field"
                      value={question.topic ?? ""}
                      onChange={(event) =>
                        updateQuestion(sectionIndex, questionIndex, { topic: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Level">
                    <select
                      className="field"
                      value={question.bloom ?? "understand"}
                      onChange={(event) =>
                        updateQuestion(sectionIndex, questionIndex, { bloom: event.target.value })
                      }
                    >
                      {BLOOM.map((level) => (
                        <option key={level} value={level}>
                          {level}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Difficulty">
                    <select
                      className="field"
                      value={question.difficulty ?? "medium"}
                      onChange={(event) =>
                        updateQuestion(sectionIndex, questionIndex, {
                          difficulty: event.target.value,
                        })
                      }
                    >
                      {DIFFICULTY.map((level) => (
                        <option key={level} value={level}>
                          {level}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>

                {(question.options ?? []).length > 0 && (
                  <div>
                    <span className="label">Options — tick the correct one</span>
                    <div className="space-y-1.5">
                      {question.options.map((option: any, optionIndex: number) => (
                        <div key={optionIndex} className="flex gap-2 items-start">
                          <input
                            type="checkbox"
                            className="mt-2.5 w-4 h-4 accent-[color:var(--color-success)] shrink-0"
                            checked={!!option.is_correct}
                            aria-label={`Option ${String.fromCharCode(65 + optionIndex)} is correct`}
                            onChange={(event) =>
                              updateQuestion(sectionIndex, questionIndex, {
                                options: question.options.map((o: any, i: number) =>
                                  i === optionIndex
                                    ? { ...o, is_correct: event.target.checked }
                                    : // A single-answer question can only have one.
                                      question.type === "mcq" && event.target.checked
                                      ? { ...o, is_correct: false }
                                      : o,
                                ),
                              })
                            }
                          />
                          <span className="pt-2 text-xs w-4 shrink-0 text-[color:var(--color-ink-faint)]">
                            {String.fromCharCode(65 + optionIndex)}
                          </span>
                          <div className="flex-1 space-y-1">
                            <input
                              className="field"
                              value={option.text ?? ""}
                              onChange={(event) =>
                                updateQuestion(sectionIndex, questionIndex, {
                                  options: question.options.map((o: any, i: number) =>
                                    i === optionIndex ? { ...o, text: event.target.value } : o,
                                  ),
                                })
                              }
                            />
                            {option.rationale && (
                              <input
                                className="field text-xs"
                                value={option.rationale}
                                placeholder="Why a student might pick this"
                                onChange={(event) =>
                                  updateQuestion(sectionIndex, questionIndex, {
                                    options: question.options.map((o: any, i: number) =>
                                      i === optionIndex
                                        ? { ...o, rationale: event.target.value }
                                        : o,
                                    ),
                                  })
                                }
                              />
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {question.type === "true_false" && (
                  <Toggle
                    checked={!!question.correct_bool}
                    onChange={(value) =>
                      updateQuestion(sectionIndex, questionIndex, { correct_bool: value })
                    }
                    label="The statement is true"
                  />
                )}

                {question.blanks !== undefined && (question.blanks ?? []).length > 0 && (
                  <div>
                    <span className="label">Accepted answers</span>
                    <ListEditor
                      items={question.blanks}
                      onChange={(blanks) =>
                        updateQuestion(sectionIndex, questionIndex, { blanks })
                      }
                    />
                  </div>
                )}

                {question.numeric_answer !== null && question.numeric_answer !== undefined && (
                  <div className="grid grid-cols-3 gap-2">
                    <Field label="Answer">
                      <input
                        type="number"
                        className="field"
                        value={question.numeric_answer}
                        onChange={(event) =>
                          updateQuestion(sectionIndex, questionIndex, {
                            numeric_answer: Number(event.target.value),
                          })
                        }
                      />
                    </Field>
                    <Field label="Unit">
                      <input
                        className="field"
                        value={question.unit ?? ""}
                        onChange={(event) =>
                          updateQuestion(sectionIndex, questionIndex, { unit: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="Tolerance">
                      <input
                        type="number"
                        step={0.01}
                        className="field"
                        value={question.tolerance ?? 0}
                        onChange={(event) =>
                          updateQuestion(sectionIndex, questionIndex, {
                            tolerance: Number(event.target.value),
                          })
                        }
                      />
                    </Field>
                  </div>
                )}

                {(question.answer !== undefined && question.answer !== "") ||
                ["short_answer", "long_answer"].includes(question.type) ? (
                  <Field label="Model answer">
                    <textarea
                      className="field min-h-20"
                      value={question.answer ?? ""}
                      onChange={(event) =>
                        updateQuestion(sectionIndex, questionIndex, { answer: event.target.value })
                      }
                    />
                  </Field>
                ) : null}

                <Field label="Explanation" hint="Printed in the answer key.">
                  <textarea
                    className="field min-h-16"
                    value={question.explanation ?? ""}
                    onChange={(event) =>
                      updateQuestion(sectionIndex, questionIndex, {
                        explanation: event.target.value,
                      })
                    }
                  />
                </Field>

                <button
                  type="button"
                  className="btn btn-danger !py-1 text-xs"
                  onClick={() =>
                    onChange({
                      ...content,
                      sections: sections.map((s, i) =>
                        i !== sectionIndex
                          ? s
                          : {
                              ...s,
                              questions: s.questions.filter(
                                (_: any, q: number) => q !== questionIndex,
                              ),
                            },
                      ),
                    })
                  }
                >
                  Delete question
                </button>
              </Collapsible>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- fallback

export function JsonEditor({
  content,
  onChange,
}: {
  content: any;
  onChange: Setter;
}) {
  const [text, setText] = useState(() => JSON.stringify(content, null, 2));
  const [error, setError] = useState("");

  return (
    <Card className="space-y-2">
      <p className="text-xs text-[color:var(--color-ink-soft)]">
        This type does not have a visual editor yet. Edit the structured content directly — it is
        validated before it saves.
      </p>
      <textarea
        className="field min-h-[28rem] font-mono text-[12.5px]"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          try {
            onChange(JSON.parse(event.target.value));
            setError("");
          } catch (exc) {
            setError(exc instanceof Error ? exc.message : "Invalid JSON");
          }
        }}
        spellCheck={false}
      />
      {error && (
        <p className="text-xs" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Card>
  );
}
