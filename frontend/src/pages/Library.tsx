import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, EmptyState, ErrorBanner, Loading, PageHeader, StatusBadge } from "../components/ui";
import { api, formatDate } from "../lib/api";

const KIND_LABELS: Record<string, string> = {
  slides: "Slides",
  notes: "Notes",
  exam: "Papers",
  worksheet: "Worksheets",
  lesson_plan: "Lesson plans",
  rubric: "Rubrics",
  flashcards: "Flashcards",
  grading: "Marking",
};

const KIND_ICONS: Record<string, string> = {
  slides: "🖼️",
  notes: "📝",
  exam: "📋",
  worksheet: "✏️",
  lesson_plan: "🗓️",
  rubric: "📊",
  flashcards: "🃏",
  grading: "✅",
};

export default function Library() {
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");

  const artifacts = useQuery({
    queryKey: ["artifacts", { kind, q: search }],
    queryFn: () => api.artifacts({ kind: kind || undefined, q: search || undefined }),
    // Poll while anything is still generating so the list settles on its own.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((a) => a.status === "generating") ? 3000 : false,
  });

  return (
    <div>
      <PageHeader
        title="Library"
        subtitle="Everything you have made. Open one to edit it or export it again."
        actions={
          <Link className="btn btn-primary" to="/generate">
            ✨ Create
          </Link>
        }
      />

      <div className="flex flex-wrap gap-2 mb-4">
        <input
          className="field max-w-xs"
          placeholder="Search titles…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select className="field max-w-45" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All types</option>
          {Object.entries(KIND_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {artifacts.error && <ErrorBanner error={artifacts.error} onRetry={artifacts.refetch} />}

      {artifacts.isLoading ? (
        <Loading />
      ) : (artifacts.data?.length ?? 0) === 0 ? (
        <Card padded={false}>
          <EmptyState
            icon="📚"
            title={search || kind ? "Nothing matches" : "Your library is empty"}
            body={
              search || kind
                ? "Try a different search or clear the filter."
                : "Generate a slide deck, some notes or a question paper to get started."
            }
            action={
              <Link className="btn btn-primary" to="/generate">
                Create something
              </Link>
            }
          />
        </Card>
      ) : (
        <ul className="grid gap-2">
          {artifacts.data!.map((artifact) => (
            <li key={artifact.id}>
              <Link
                to={`/artifacts/${artifact.id}`}
                className="card px-4 py-3 flex items-center gap-3 hover:border-[color:var(--color-brand)] transition-colors"
              >
                <span className="text-lg shrink-0" aria-hidden="true">
                  {KIND_ICONS[artifact.kind] ?? "📄"}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-sm truncate">
                    {artifact.title || "Untitled"}
                  </div>
                  <div className="text-xs text-[color:var(--color-ink-faint)] flex flex-wrap gap-x-3">
                    <span>{KIND_LABELS[artifact.kind] ?? artifact.kind}</span>
                    <span>{formatDate(artifact.created_at)}</span>
                    {artifact.model_used && <span>{artifact.model_used}</span>}
                    {artifact.version > 1 && <span>edited</span>}
                  </div>
                </div>
                <StatusBadge status={artifact.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
