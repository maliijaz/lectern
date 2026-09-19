import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Card, Loading, PageHeader, StatusBadge } from "../components/ui";
import { api, formatDate } from "../lib/api";
import type { ArtifactKind } from "../lib/types";

const QUICK_ACTIONS: { kind: ArtifactKind; label: string; body: string; icon: string }[] = [
  { kind: "slides", label: "Slide deck", body: "A lecture with speaker notes", icon: "🖼️" },
  { kind: "notes", label: "Lecture notes", body: "Notes students can revise from", icon: "📝" },
  { kind: "exam", label: "Question paper", body: "Built to a marks blueprint", icon: "📋" },
  { kind: "worksheet", label: "Worksheet", body: "Practice with an answer key", icon: "✏️" },
  { kind: "lesson_plan", label: "Lesson plan", body: "Timed, with differentiation", icon: "🗓️" },
  { kind: "rubric", label: "Rubric", body: "Observable criteria", icon: "📊" },
  { kind: "flashcards", label: "Flashcards", body: "Anki and Quizlet decks", icon: "🃏" },
  { kind: "grading", label: "Grade work", body: "A first-pass marking proposal", icon: "✅" },
];

export default function Dashboard() {
  const artifacts = useQuery({
    queryKey: ["artifacts", { limit: 8 }],
    queryFn: () => api.artifacts({}),
  });
  const stats = useQuery({ queryKey: ["library-stats"], queryFn: api.libraryStats });
  const running = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () => api.jobs({ status: "running", limit: 5 }),
    refetchInterval: 4000,
  });

  const recent = (artifacts.data ?? []).slice(0, 6);

  return (
    <div className="space-y-7">
      <PageHeader
        title="What would you like to make?"
        subtitle="Start from a topic, or from documents you have already added."
      />

      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        {QUICK_ACTIONS.map((action) => (
          <Link
            key={action.kind}
            to={`/generate/${action.kind}`}
            className="card p-4 hover:border-[color:var(--color-brand)] transition-colors group"
          >
            <div className="text-2xl mb-2" aria-hidden="true">
              {action.icon}
            </div>
            <div className="font-semibold text-sm group-hover:text-[color:var(--color-brand)]">
              {action.label}
            </div>
            <div className="text-xs text-[color:var(--color-ink-soft)] mt-0.5">{action.body}</div>
          </Link>
        ))}
      </div>

      {(running.data?.length ?? 0) > 0 && (
        <Card>
          <h2 className="font-semibold text-sm mb-3">In progress</h2>
          <ul className="space-y-2">
            {running.data!.map((job) => (
              <li key={job.id} className="flex items-center gap-3 text-sm">
                <StatusBadge status={job.status} />
                <span className="flex-1 truncate text-[color:var(--color-ink-soft)]">
                  {job.message || job.kind.replace(/_/g, " ")}
                </span>
                {job.artifact_id && (
                  <Link className="btn !py-1 !px-2 text-xs" to={`/artifacts/${job.artifact_id}`}>
                    View
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <div className="text-xs text-[color:var(--color-ink-soft)]">Source documents</div>
          <div className="text-2xl font-bold tabular-nums mt-0.5">
            {stats.data?.documents ?? "—"}
          </div>
          <Link
            to="/documents"
            className="text-xs text-[color:var(--color-brand)] mt-1.5 inline-block"
          >
            Manage sources →
          </Link>
        </Card>
        <Card>
          <div className="text-xs text-[color:var(--color-ink-soft)]">Words indexed</div>
          <div className="text-2xl font-bold tabular-nums mt-0.5">
            {stats.data ? stats.data.words.toLocaleString() : "—"}
          </div>
          <div className="text-xs text-[color:var(--color-ink-faint)] mt-1.5">
            {stats.data?.chunks ?? 0} searchable passages
          </div>
        </Card>
        <Card>
          <div className="text-xs text-[color:var(--color-ink-soft)]">Made so far</div>
          <div className="text-2xl font-bold tabular-nums mt-0.5">
            {artifacts.data?.length ?? "—"}
          </div>
          <Link
            to="/library"
            className="text-xs text-[color:var(--color-brand)] mt-1.5 inline-block"
          >
            Open library →
          </Link>
        </Card>
      </div>

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold">Recent</h2>
          <Link to="/library" className="text-sm text-[color:var(--color-brand)]">
            See all
          </Link>
        </div>

        {artifacts.isLoading ? (
          <Loading />
        ) : recent.length === 0 ? (
          <Card>
            <p className="text-sm text-[color:var(--color-ink-soft)] text-center py-6">
              Nothing yet. Pick something above to get started.
            </p>
          </Card>
        ) : (
          <ul className="space-y-2">
            {recent.map((artifact) => (
              <li key={artifact.id}>
                <Link
                  to={`/artifacts/${artifact.id}`}
                  className="card px-4 py-3 flex items-center gap-3 hover:border-[color:var(--color-brand)] transition-colors"
                >
                  <span className="chip">{artifact.kind.replace(/_/g, " ")}</span>
                  <span className="flex-1 truncate font-medium text-sm">
                    {artifact.title || "Untitled"}
                  </span>
                  <StatusBadge status={artifact.status} />
                  <span className="text-xs text-[color:var(--color-ink-faint)] hidden sm:inline tabular-nums">
                    {formatDate(artifact.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
