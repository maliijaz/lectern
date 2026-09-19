import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Card, EmptyState, ErrorBanner, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/api";

const TYPE_LABELS: Record<string, string> = {
  mcq: "Multiple choice",
  multi_select: "Multiple answer",
  true_false: "True / false",
  fill_blank: "Fill the blank",
  matching: "Matching",
  assertion_reason: "Assertion & reason",
  numerical: "Numerical",
  short_answer: "Short answer",
  long_answer: "Long answer",
  case_study: "Case study",
  diagram_label: "Diagram label",
};

export default function QuestionBank() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ q: "", qtype: "", bloom: "", difficulty: "", topic: "" });
  const [expanded, setExpanded] = useState<string | null>(null);

  const questions = useQuery({
    queryKey: ["bank", filters],
    queryFn: () => api.bank({ ...filters, limit: 100 }),
  });
  const facets = useQuery({ queryKey: ["bank-facets"], queryFn: api.bankFacets });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteBankQuestion(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bank"] });
      queryClient.invalidateQueries({ queryKey: ["bank-facets"] });
    },
  });

  const set = (key: keyof typeof filters, value: string) =>
    setFilters((previous) => ({ ...previous, [key]: value }));

  return (
    <div>
      <PageHeader
        title="Question bank"
        subtitle="Every question you have generated, kept for reuse. Next term's revision paper does not need a model at all."
      />

      <Card className="mb-5 space-y-3">
        <input
          className="field"
          placeholder="Search question text…"
          value={filters.q}
          onChange={(event) => set("q", event.target.value)}
        />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <select className="field" value={filters.qtype} onChange={(e) => set("qtype", e.target.value)}>
            <option value="">Any type</option>
            {Object.entries(facets.data?.types ?? {}).map(([value, count]) => (
              <option key={value} value={value}>
                {TYPE_LABELS[value] ?? value} ({count})
              </option>
            ))}
          </select>
          <select className="field" value={filters.bloom} onChange={(e) => set("bloom", e.target.value)}>
            <option value="">Any level</option>
            {Object.entries(facets.data?.bloom ?? {}).map(([value, count]) => (
              <option key={value} value={value}>
                {value} ({count})
              </option>
            ))}
          </select>
          <select
            className="field"
            value={filters.difficulty}
            onChange={(e) => set("difficulty", e.target.value)}
          >
            <option value="">Any difficulty</option>
            {Object.entries(facets.data?.difficulty ?? {}).map(([value, count]) => (
              <option key={value} value={value}>
                {value} ({count})
              </option>
            ))}
          </select>
          <select className="field" value={filters.topic} onChange={(e) => set("topic", e.target.value)}>
            <option value="">Any topic</option>
            {Object.entries(facets.data?.topics ?? {}).map(([value, count]) => (
              <option key={value} value={value}>
                {value} ({count})
              </option>
            ))}
          </select>
        </div>
        {facets.data && (
          <p className="text-xs text-[color:var(--color-ink-faint)]">
            {facets.data.total} question{facets.data.total === 1 ? "" : "s"} banked.
          </p>
        )}
      </Card>

      {questions.error && <ErrorBanner error={questions.error} onRetry={questions.refetch} />}

      {questions.isLoading ? (
        <Loading />
      ) : (questions.data?.length ?? 0) === 0 ? (
        <Card padded={false}>
          <EmptyState
            icon="❓"
            title="Nothing banked yet"
            body="Questions are saved here automatically whenever you generate a paper or a worksheet."
          />
        </Card>
      ) : (
        <ul className="space-y-2">
          {questions.data!.map((question) => {
            const open = expanded === question.id;
            const payload = question.payload as any;
            return (
              <li key={question.id} className="card">
                <button
                  type="button"
                  className="w-full text-left px-4 py-3"
                  onClick={() => setExpanded(open ? null : question.id)}
                >
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <span className="chip">{TYPE_LABELS[question.qtype] ?? question.qtype}</span>
                    <span className="chip">{question.bloom}</span>
                    <span className="chip">{question.difficulty}</span>
                    <span className="chip">{question.marks} marks</span>
                    {question.topic && (
                      <span className="text-xs text-[color:var(--color-ink-faint)]">
                        {question.topic}
                      </span>
                    )}
                  </div>
                  <p className="text-sm">{question.stem}</p>
                </button>

                {open && (
                  <div className="px-4 pb-4 border-t border-[color:var(--color-line)] pt-3 space-y-2 text-sm">
                    {(payload.options ?? []).map((option: any, index: number) => (
                      <div
                        key={index}
                        className="flex gap-2"
                        style={{ color: option.is_correct ? "var(--color-success)" : undefined }}
                      >
                        <span className="font-mono text-xs pt-0.5 shrink-0">
                          {String.fromCharCode(65 + index)}.
                        </span>
                        <div>
                          <span>{option.text}</span>
                          {option.is_correct && <span className="ml-1.5">✓</span>}
                          {option.rationale && (
                            <p className="text-xs text-[color:var(--color-ink-faint)]">
                              {option.rationale}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}

                    {payload.answer && (
                      <p>
                        <strong>Answer:</strong> {payload.answer}
                      </p>
                    )}
                    {payload.working && (
                      <p className="whitespace-pre-wrap">
                        <strong>Working:</strong> {payload.working}
                      </p>
                    )}
                    {payload.explanation && (
                      <p className="text-[color:var(--color-ink-soft)]">{payload.explanation}</p>
                    )}
                    {(payload.rubric_points ?? []).length > 0 && (
                      <div>
                        <strong>Mark scheme</strong>
                        <ul className="list-disc pl-5">
                          {payload.rubric_points.map((point: any, index: number) => (
                            <li key={index}>
                              {point.description} ({point.marks})
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <button
                      type="button"
                      className="btn btn-danger !py-1 text-xs"
                      onClick={() => remove.mutate(question.id)}
                    >
                      Remove from bank
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
