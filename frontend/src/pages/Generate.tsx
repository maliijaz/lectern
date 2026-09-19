/** The generation wizard.
 *
 * The form is hand-written per kind rather than generated from the JSON Schema. A schema
 * form would be less code and a worse product: it would show `bloom_mix` as a raw JSON
 * blob and `include_quiz_slides` as "Include Quiz Slides", and a teacher would have no
 * idea what either means. Every field here is labelled in the words a teacher uses.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Card,
  ErrorBanner,
  Field,
  PageHeader,
  ProgressBar,
  Spinner,
  Toggle,
} from "../components/ui";
import { api } from "../lib/api";
import type { ArtifactKind, Doc, Theme } from "../lib/types";
import { useJob } from "../lib/useJob";

const KINDS: { kind: ArtifactKind; label: string; icon: string; blurb: string }[] = [
  { kind: "slides", label: "Slide deck", icon: "🖼️", blurb: "A lecture with speaker notes" },
  { kind: "notes", label: "Lecture notes", icon: "📝", blurb: "Notes students revise from" },
  { kind: "exam", label: "Question paper", icon: "📋", blurb: "Built to a marks blueprint" },
  { kind: "worksheet", label: "Worksheet", icon: "✏️", blurb: "Practice with an answer key" },
  { kind: "lesson_plan", label: "Lesson plan", icon: "🗓️", blurb: "Timed, with differentiation" },
  { kind: "rubric", label: "Rubric", icon: "📊", blurb: "Observable criteria" },
  { kind: "flashcards", label: "Flashcards", icon: "🃏", blurb: "Anki and Quizlet decks" },
  { kind: "grading", label: "Grade work", icon: "✅", blurb: "A first-pass marking proposal" },
];

const QUESTION_TYPES = [
  { value: "mcq", label: "Multiple choice" },
  { value: "multi_select", label: "Multiple answer" },
  { value: "true_false", label: "True / false" },
  { value: "fill_blank", label: "Fill in the blank" },
  { value: "matching", label: "Matching" },
  { value: "assertion_reason", label: "Assertion & reason" },
  { value: "numerical", label: "Numerical" },
  { value: "short_answer", label: "Short answer" },
  { value: "long_answer", label: "Long answer / essay" },
  { value: "case_study", label: "Case study" },
];

interface FormState {
  topic: string;
  document_ids: string[];
  instructions: string;
  subject: string;
  grade_level: string;
  language: string;
  reading_level: string;
  // slides
  slide_count: number;
  lecture_minutes: number;
  theme: string;
  include_quiz_slides: boolean;
  include_diagrams: boolean;
  // notes
  depth: string;
  max_sections: number;
  include_misconceptions: boolean;
  // exam
  total_marks: number;
  duration_minutes: number;
  exam_name: string;
  institution: string;
  question_types: string[];
  variants: number;
  negative_marking: number;
  // worksheet
  question_count: number;
  difficulty: string;
  // lesson plan
  template: string;
  plan_minutes: number;
  // rubric
  task_description: string;
  criteria_count: number;
  style: string;
  // flashcards
  card_count: number;
  include_cloze: boolean;
  // grading
  student_work: string;
  student_identifier: string;
  strictness: string;
  max_points: number;
}

const INITIAL: FormState = {
  topic: "",
  document_ids: [],
  instructions: "",
  subject: "",
  grade_level: "",
  language: "English",
  reading_level: "",
  slide_count: 12,
  lecture_minutes: 0,
  theme: "academic",
  include_quiz_slides: true,
  include_diagrams: true,
  depth: "standard",
  max_sections: 8,
  include_misconceptions: true,
  total_marks: 50,
  duration_minutes: 60,
  exam_name: "",
  institution: "",
  question_types: ["mcq", "short_answer", "long_answer"],
  variants: 1,
  negative_marking: 0,
  question_count: 12,
  difficulty: "medium",
  template: "generic",
  plan_minutes: 45,
  task_description: "",
  criteria_count: 4,
  style: "analytic",
  card_count: 25,
  include_cloze: true,
  student_work: "",
  student_identifier: "",
  strictness: "balanced",
  max_points: 0,
};

export default function Generate() {
  const { kind: kindParam } = useParams<{ kind: string }>();
  const navigate = useNavigate();
  const [kind, setKind] = useState<ArtifactKind>((kindParam as ArtifactKind) ?? "slides");
  const [form, setForm] = useState<FormState>(INITIAL);
  const [jobId, setJobId] = useState<string | null>(null);
  const [artifactId, setArtifactId] = useState<string | null>(null);

  useEffect(() => {
    if (kindParam) setKind(kindParam as ArtifactKind);
  }, [kindParam]);

  const documents = useQuery({ queryKey: ["documents"], queryFn: () => api.documents({}) });
  const themes = useQuery({ queryKey: ["themes"], queryFn: api.themes });

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((previous) => ({ ...previous, [key]: value }));

  const job = useJob(jobId, (state) => {
    if (state.status === "succeeded" && artifactId) {
      navigate(`/artifacts/${artifactId}`);
    }
  });

  const mutation = useMutation({
    mutationFn: () => api.generate(kind, buildParams(kind, form)),
    onSuccess: (data) => {
      setArtifactId(data.artifact.id);
      setJobId(data.job_id);
    },
  });

  const ready = documents.data?.filter((d) => d.status === "ready") ?? [];
  const busy = mutation.isPending || (jobId !== null && !job.done);
  const needsSource =
    kind === "grading" ? !form.student_work.trim() : !form.topic.trim() && form.document_ids.length === 0;

  return (
    <div className="max-w-3xl mx-auto">
      <PageHeader title="Create" subtitle="Pick what to make, then say what it is about." />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-6">
        {KINDS.map((option) => {
          const selected = option.kind === kind;
          return (
            <button
              key={option.kind}
              type="button"
              onClick={() => {
                setKind(option.kind);
                navigate(`/generate/${option.kind}`, { replace: true });
              }}
              className="card p-3 text-left transition-colors"
              style={{
                borderColor: selected ? "var(--color-brand)" : "var(--color-line)",
                background: selected ? "var(--color-brand-soft)" : "var(--color-paper)",
              }}
            >
              <div className="text-lg" aria-hidden="true">
                {option.icon}
              </div>
              <div className="text-sm font-semibold mt-1">{option.label}</div>
              <div className="text-xs text-[color:var(--color-ink-soft)] leading-snug mt-0.5">
                {option.blurb}
              </div>
            </button>
          );
        })}
      </div>

      <form
        className="space-y-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (!needsSource) mutation.mutate();
        }}
      >
        <Card className="space-y-4">
          {kind === "grading" ? (
            <>
              <Field label="The student's work" hint="Paste the answer to be marked.">
                <textarea
                  className="field min-h-40 font-mono text-[13px]"
                  value={form.student_work}
                  onChange={(event) => set("student_work", event.target.value)}
                  placeholder="Paste the student's answer here…"
                />
              </Field>
              <div className="grid sm:grid-cols-2 gap-3">
                <Field label="Student name or ID" hint="Optional.">
                  <input
                    className="field"
                    value={form.student_identifier}
                    onChange={(event) => set("student_identifier", event.target.value)}
                  />
                </Field>
                <Field label="Marks available" hint="0 to take it from the rubric.">
                  <input
                    type="number"
                    min={0}
                    className="field"
                    value={form.max_points}
                    onChange={(event) => set("max_points", Number(event.target.value))}
                  />
                </Field>
              </div>
              <Field
                label="What was asked, and how to mark it"
                hint="Paste the question and the mark scheme, or pick a rubric from your library below."
              >
                <textarea
                  className="field min-h-24"
                  value={form.task_description}
                  onChange={(event) => set("task_description", event.target.value)}
                />
              </Field>
              <Field label="How strictly to mark">
                <select
                  className="field"
                  value={form.strictness}
                  onChange={(event) => set("strictness", event.target.value)}
                >
                  <option value="lenient">Lenient — credit the intent</option>
                  <option value="balanced">Balanced — credit what is shown</option>
                  <option value="strict">Strict — credit only what is explicit</option>
                </select>
              </Field>
              <p className="text-xs text-[color:var(--color-ink-soft)]">
                Marking is assistive. Every judgement comes with the evidence behind it, and
                borderline work is flagged for you rather than scored quietly.
              </p>
            </>
          ) : (
            <>
              <Field
                label={kind === "rubric" ? "The assignment being assessed" : "Topic"}
                hint={
                  kind === "rubric"
                    ? "Describe the task students will hand in."
                    : "What is this about? Leave blank if you are working only from documents."
                }
              >
                <input
                  className="field"
                  value={kind === "rubric" ? form.task_description : form.topic}
                  onChange={(event) =>
                    kind === "rubric"
                      ? set("task_description", event.target.value)
                      : set("topic", event.target.value)
                  }
                  placeholder={
                    kind === "rubric"
                      ? "A lab report on the rate of photosynthesis"
                      : "Photosynthesis: the light-dependent reactions"
                  }
                />
              </Field>

              <DocumentPicker
                documents={ready}
                selected={form.document_ids}
                onChange={(ids) => set("document_ids", ids)}
                loading={documents.isLoading}
              />
            </>
          )}
        </Card>

        <Card className="space-y-4">
          <h2 className="font-semibold text-sm">Who it is for</h2>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Subject">
              <input
                className="field"
                value={form.subject}
                onChange={(event) => set("subject", event.target.value)}
                placeholder="Biology"
              />
            </Field>
            <Field label="Class or grade">
              <input
                className="field"
                value={form.grade_level}
                onChange={(event) => set("grade_level", event.target.value)}
                placeholder="Grade 10"
              />
            </Field>
            <Field label="Language">
              <input
                className="field"
                value={form.language}
                onChange={(event) => set("language", event.target.value)}
              />
            </Field>
          </div>
          {(kind === "notes" || kind === "worksheet") && (
            <Field
              label="Reading level"
              hint="Leave blank to match the class. Set it lower to support weaker readers."
            >
              <input
                className="field"
                value={form.reading_level}
                onChange={(event) => set("reading_level", event.target.value)}
                placeholder="Grade 7"
              />
            </Field>
          )}
        </Card>

        <KindOptions kind={kind} form={form} set={set} themes={themes.data ?? []} />

        <Card>
          <Field
            label="Anything else"
            hint="Your own direction, in your own words — it goes straight to the model."
          >
            <textarea
              className="field min-h-20"
              value={form.instructions}
              onChange={(event) => set("instructions", event.target.value)}
              placeholder="Focus on the practical applications. Use examples from agriculture."
            />
          </Field>
        </Card>

        {mutation.error && <ErrorBanner error={mutation.error} />}

        {jobId && !job.done && (
          <Card>
            <ProgressBar value={job.progress} label={job.message || "Working…"} />
            <p className="text-xs text-[color:var(--color-ink-soft)] mt-2">
              This takes a few minutes on a local model. You can leave this page — it keeps
              running, and you will find it in your library.
            </p>
          </Card>
        )}

        {job.status === "failed" && <ErrorBanner error={new Error(job.error)} />}

        <div className="flex items-center gap-3">
          <button className="btn btn-primary" type="submit" disabled={busy || needsSource}>
            {busy ? <Spinner /> : "✨"}
            {busy ? "Generating…" : "Generate"}
          </button>
          {needsSource && (
            <span className="text-xs text-[color:var(--color-ink-soft)]">
              {kind === "grading"
                ? "Paste the student's work to continue."
                : "Enter a topic or choose a document."}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

function DocumentPicker({
  documents,
  selected,
  onChange,
  loading,
}: {
  documents: Doc[];
  selected: string[];
  onChange: (ids: string[]) => void;
  loading: boolean;
}) {
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <div>
      <span className="label">Base it on your documents</span>
      {loading ? (
        <div className="text-sm text-[color:var(--color-ink-soft)] py-2">Loading…</div>
      ) : documents.length === 0 ? (
        <p className="text-sm text-[color:var(--color-ink-soft)] py-1">
          No documents yet. Add some under <strong>Sources</strong> to ground the output in your
          own material, or just use a topic.
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5 mt-1">
          {documents.map((document) => {
            const active = selected.includes(document.id);
            return (
              <button
                key={document.id}
                type="button"
                onClick={() => toggle(document.id)}
                className="chip transition-colors"
                style={{
                  background: active ? "var(--color-brand-soft)" : "var(--color-surface-2)",
                  color: active ? "var(--color-brand)" : "var(--color-ink-soft)",
                  cursor: "pointer",
                }}
              >
                {active && <span aria-hidden="true">✓</span>}
                {document.title || document.original_name}
              </button>
            );
          })}
        </div>
      )}
      {selected.length > 0 && (
        <p className="text-xs text-[color:var(--color-ink-faint)] mt-1.5">
          Output will cite these sources.
        </p>
      )}
    </div>
  );
}

function KindOptions({
  kind,
  form,
  set,
  themes,
}: {
  kind: ArtifactKind;
  form: FormState;
  set: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
  themes: Theme[];
}) {
  if (kind === "grading") return null;

  return (
    <Card className="space-y-4">
      <h2 className="font-semibold text-sm">Options</h2>

      {kind === "slides" && (
        <>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Slides" hint="Ignored if you set a lecture length.">
              <input
                type="number"
                min={3}
                max={80}
                className="field"
                value={form.slide_count}
                onChange={(event) => set("slide_count", Number(event.target.value))}
              />
            </Field>
            <Field label="Lecture length" hint="Minutes. 0 to set slides directly.">
              <input
                type="number"
                min={0}
                className="field"
                value={form.lecture_minutes}
                onChange={(event) => set("lecture_minutes", Number(event.target.value))}
              />
            </Field>
            <Field label="Theme">
              <select
                className="field"
                value={form.theme}
                onChange={(event) => set("theme", event.target.value)}
              >
                {themes.map((theme) => (
                  <option key={theme.key} value={theme.key}>
                    {theme.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Toggle
            checked={form.include_quiz_slides}
            onChange={(value) => set("include_quiz_slides", value)}
            label="Insert check-for-understanding slides"
            hint="Spread through the deck rather than saved for the end."
          />
          <Toggle
            checked={form.include_diagrams}
            onChange={(value) => set("include_diagrams", value)}
            label="Let it specify diagrams"
          />
        </>
      )}

      {kind === "notes" && (
        <>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Depth">
              <select
                className="field"
                value={form.depth}
                onChange={(event) => set("depth", event.target.value)}
              >
                <option value="outline">Outline — the skeleton</option>
                <option value="standard">Standard — revisable notes</option>
                <option value="detailed">Detailed — worked reasoning</option>
                <option value="textbook">Textbook — full treatment</option>
              </select>
            </Field>
            <Field label="Maximum sections">
              <input
                type="number"
                min={1}
                max={30}
                className="field"
                value={form.max_sections}
                onChange={(event) => set("max_sections", Number(event.target.value))}
              />
            </Field>
          </div>
          <Toggle
            checked={form.include_misconceptions}
            onChange={(value) => set("include_misconceptions", value)}
            label="Call out common misconceptions"
            hint="Names what students typically get wrong, and why."
          />
        </>
      )}

      {kind === "exam" && (
        <>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Exam name">
              <input
                className="field"
                value={form.exam_name}
                onChange={(event) => set("exam_name", event.target.value)}
                placeholder="Mid-Term Examination"
              />
            </Field>
            <Field label="School or university">
              <input
                className="field"
                value={form.institution}
                onChange={(event) => set("institution", event.target.value)}
              />
            </Field>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Total marks">
              <input
                type="number"
                min={1}
                className="field"
                value={form.total_marks}
                onChange={(event) => set("total_marks", Number(event.target.value))}
              />
            </Field>
            <Field label="Duration" hint="Minutes.">
              <input
                type="number"
                min={5}
                className="field"
                value={form.duration_minutes}
                onChange={(event) => set("duration_minutes", Number(event.target.value))}
              />
            </Field>
            <Field label="Variants" hint="Shuffled sets A, B, C…">
              <input
                type="number"
                min={1}
                max={10}
                className="field"
                value={form.variants}
                onChange={(event) => set("variants", Number(event.target.value))}
              />
            </Field>
          </div>
          <div>
            <span className="label">Question types</span>
            <div className="flex flex-wrap gap-1.5">
              {QUESTION_TYPES.map((type) => {
                const active = form.question_types.includes(type.value);
                return (
                  <button
                    key={type.value}
                    type="button"
                    className="chip"
                    style={{
                      background: active ? "var(--color-brand-soft)" : "var(--color-surface-2)",
                      color: active ? "var(--color-brand)" : "var(--color-ink-soft)",
                      cursor: "pointer",
                    }}
                    onClick={() =>
                      set(
                        "question_types",
                        active
                          ? form.question_types.filter((x) => x !== type.value)
                          : [...form.question_types, type.value],
                      )
                    }
                  >
                    {active && <span aria-hidden="true">✓</span>}
                    {type.label}
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-[color:var(--color-ink-faint)] mt-1.5">
              Marks are spread across topics and cognitive levels automatically, then the finished
              paper is checked against that plan.
            </p>
          </div>
          <Field
            label="Negative marking"
            hint="Marks deducted per wrong objective answer. 0 for none."
          >
            <input
              type="number"
              min={0}
              step={0.25}
              className="field"
              value={form.negative_marking}
              onChange={(event) => set("negative_marking", Number(event.target.value))}
            />
          </Field>
        </>
      )}

      {kind === "worksheet" && (
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Questions">
            <input
              type="number"
              min={1}
              max={100}
              className="field"
              value={form.question_count}
              onChange={(event) => set("question_count", Number(event.target.value))}
            />
          </Field>
          <Field label="Difficulty">
            <select
              className="field"
              value={form.difficulty}
              onChange={(event) => set("difficulty", event.target.value)}
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </Field>
        </div>
      )}

      {kind === "lesson_plan" && (
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Period length" hint="Minutes.">
            <input
              type="number"
              min={5}
              max={480}
              className="field"
              value={form.plan_minutes}
              onChange={(event) => set("plan_minutes", Number(event.target.value))}
            />
          </Field>
          <Field label="Template">
            <select
              className="field"
              value={form.template}
              onChange={(event) => set("template", event.target.value)}
            >
              <option value="generic">Generic</option>
              <option value="5e">5E — engage, explore, explain…</option>
              <option value="hunter">Madeline Hunter</option>
              <option value="gradual_release">Gradual release — I do, we do, you do</option>
              <option value="inquiry">Inquiry</option>
            </select>
          </Field>
        </div>
      )}

      {kind === "rubric" && (
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Criteria">
            <input
              type="number"
              min={2}
              max={12}
              className="field"
              value={form.criteria_count}
              onChange={(event) => set("criteria_count", Number(event.target.value))}
            />
          </Field>
          <Field label="Style">
            <select
              className="field"
              value={form.style}
              onChange={(event) => set("style", event.target.value)}
            >
              <option value="analytic">Analytic — a grid</option>
              <option value="holistic">Holistic — one judgement</option>
              <option value="single_point">Single point — the standard only</option>
            </select>
          </Field>
        </div>
      )}

      {kind === "flashcards" && (
        <>
          <Field label="Cards">
            <input
              type="number"
              min={1}
              max={300}
              className="field"
              value={form.card_count}
              onChange={(event) => set("card_count", Number(event.target.value))}
            />
          </Field>
          <Toggle
            checked={form.include_cloze}
            onChange={(value) => set("include_cloze", value)}
            label="Include fill-the-gap cards"
          />
        </>
      )}
    </Card>
  );
}

function buildParams(kind: ArtifactKind, form: FormState): Record<string, unknown> {
  const audience = {
    subject: form.subject,
    grade_level: form.grade_level,
    language: form.language,
    reading_level: form.reading_level,
  };
  const base = {
    topic: form.topic,
    document_ids: form.document_ids,
    instructions: form.instructions,
    audience,
  };

  switch (kind) {
    case "slides":
      return {
        ...base,
        slide_count: form.slide_count,
        lecture_minutes: form.lecture_minutes,
        theme: form.theme,
        include_quiz_slides: form.include_quiz_slides,
        include_diagrams: form.include_diagrams,
      };
    case "notes":
      return {
        ...base,
        depth: form.depth,
        max_sections: form.max_sections,
        include_misconceptions: form.include_misconceptions,
      };
    case "exam":
      return {
        ...base,
        total_marks: form.total_marks,
        duration_minutes: form.duration_minutes,
        exam_name: form.exam_name,
        institution: form.institution,
        question_types: form.question_types,
        variants: form.variants,
        negative_marking: form.negative_marking,
      };
    case "worksheet":
      return { ...base, question_count: form.question_count, difficulty: form.difficulty };
    case "lesson_plan":
      return { ...base, duration_minutes: form.plan_minutes, template: form.template };
    case "rubric":
      return {
        ...base,
        topic: form.task_description || form.topic,
        task_description: form.task_description,
        criteria_count: form.criteria_count,
        style: form.style,
      };
    case "flashcards":
      return { ...base, card_count: form.card_count, include_cloze: form.include_cloze };
    case "grading":
      return {
        student_work: form.student_work,
        student_identifier: form.student_identifier,
        task_description: form.task_description,
        max_points: form.max_points,
        strictness: form.strictness,
        audience,
      };
    default:
      return base;
  }
}
