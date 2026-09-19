import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DeckEditor, JsonEditor, NotesEditor, PaperEditor } from "../components/editors";
import {
  Card,
  ErrorBanner,
  Field,
  Loading,
  Modal,
  PageHeader,
  ProgressBar,
  Spinner,
  StatusBadge,
  Tabs,
  Toggle,
} from "../components/ui";
import { api, formatBytes } from "../lib/api";
import type { ArtifactFile } from "../lib/types";
import { useJob } from "../lib/useJob";

export default function ArtifactDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState("edit");
  const [draft, setDraft] = useState<any>(null);
  const [adaptOpen, setAdaptOpen] = useState(false);
  const [adaptJobId, setAdaptJobId] = useState<string | null>(null);

  const artifact = useQuery({
    queryKey: ["artifact", id],
    queryFn: () => api.artifact(id),
    // Keep refreshing while it is still being generated.
    refetchInterval: (query) => (query.state.data?.status === "generating" ? 2500 : false),
  });

  const formats = useQuery({
    queryKey: ["formats", id],
    queryFn: () => api.formats(id),
    enabled: artifact.data?.status === "ready",
  });

  useEffect(() => {
    if (artifact.data && draft === null) setDraft(structuredClone(artifact.data.content));
  }, [artifact.data, draft]);

  const save = useMutation({
    mutationFn: () => api.updateContent(id, draft, draft?.title),
    onSuccess: (updated) => {
      queryClient.setQueryData(["artifact", id], updated);
      queryClient.invalidateQueries({ queryKey: ["artifacts"] });
      setDraft(structuredClone(updated.content));
    },
  });

  const exportMutation = useMutation({
    mutationFn: (payload: { formats: string[]; force?: boolean }) =>
      api.exportArtifact(id, payload.formats, { force: payload.force }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["artifact", id] }),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteArtifact(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["artifacts"] });
      navigate("/library");
    },
  });

  const adaptJob = useJob(adaptJobId, (state) => {
    if (state.status === "succeeded") {
      const newId = (state.result as any)?.artifact_id;
      setAdaptJobId(null);
      setAdaptOpen(false);
      if (newId) navigate(`/artifacts/${newId}`);
    }
  });

  if (artifact.isLoading) return <Loading label="Opening" />;
  if (artifact.error) return <ErrorBanner error={artifact.error} onRetry={artifact.refetch} />;
  if (!artifact.data) return null;

  const data = artifact.data;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(data.content);
  const report = data.report ?? {};
  const disputed = report.answer_disagreements ?? [];

  if (data.status === "generating") {
    return (
      <div className="max-w-2xl mx-auto">
        <PageHeader title={data.title || "Generating…"} />
        <Card className="text-center py-10">
          <Spinner size={28} />
          <p className="mt-3 font-medium">Still working</p>
          <p className="text-sm text-[color:var(--color-ink-soft)] mt-1">
            This page updates on its own. You can safely close it.
          </p>
        </Card>
      </div>
    );
  }

  if (data.status === "failed") {
    return (
      <div className="max-w-2xl mx-auto space-y-4">
        <PageHeader title={data.title || "Generation failed"} />
        <ErrorBanner error={new Error(data.error || "The generation failed.")} />
        <div className="flex gap-2">
          <button
            className="btn btn-primary"
            onClick={async () => {
              const result = await api.regenerate(id);
              navigate(`/artifacts/${result.artifact.id}`);
            }}
          >
            Try again
          </button>
          <button className="btn btn-danger" onClick={() => remove.mutate()}>
            Delete
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={data.title || "Untitled"}
        subtitle={`${data.kind.replace(/_/g, " ")} · version ${data.version}${
          data.model_used ? ` · ${data.model_used}` : ""
        }`}
        actions={
          <>
            <StatusBadge status={data.status} />
            <button className="btn" onClick={() => setAdaptOpen(true)}>
              🌍 Adapt
            </button>
            <button
              className="btn"
              onClick={async () => {
                const result = await api.regenerate(id);
                navigate(`/artifacts/${result.artifact.id}`);
              }}
            >
              ↻ Regenerate
            </button>
            <button
              className="btn btn-danger"
              onClick={() => {
                if (confirm("Delete this and its exported files?")) remove.mutate();
              }}
            >
              Delete
            </button>
          </>
        }
      />

      {/* A disputed answer key outranks everything else on this page. A paper weighted
          60/40 instead of 50/50 is imperfect; a paper with a wrong answer is harmful. */}
      {disputed.length > 0 && (
        <div
          className="rounded-lg px-3.5 py-3 text-sm mb-4"
          style={{
            background: "var(--color-danger-soft)",
            color: "var(--color-danger)",
            border: "1px solid color-mix(in srgb, var(--color-danger) 35%, transparent)",
          }}
        >
          <p className="font-semibold">
            Check {disputed.length} answer{disputed.length > 1 ? "s" : ""} before you use this
            paper.
          </p>
          <p className="text-[color:var(--color-ink-soft)] mt-0.5">
            Every machine-markable question was answered again independently, without showing
            the marked answer. These disagreed — either the key is wrong, or more than one
            option is defensible.
          </p>
          <ul className="mt-2 space-y-2">
            {disputed.map((item) => (
              <li key={item.number} className="text-[color:var(--color-ink)]">
                <span className="font-medium">Q{item.number}.</span> {item.question}
                <div className="text-xs text-[color:var(--color-ink-soft)] mt-0.5">
                  Key says <strong>{item.key_answer}</strong>; the second opinion said{" "}
                  <strong>{item.independent_answer}</strong> ({Math.round(item.confidence * 100)}%
                  confident). {item.reason}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.answers_ok && (report.answers_checked ?? 0) > 0 && disputed.length === 0 && (
        <div
          className="rounded-lg px-3.5 py-2 text-sm mb-4"
          style={{ background: "var(--color-success-soft)", color: "var(--color-success)" }}
        >
          ✓ {report.answers_summary}
        </div>
      )}

      {report.blueprint_matches === false && (
        <div
          className="rounded-lg px-3.5 py-3 text-sm mb-4"
          style={{ background: "var(--color-warn-soft)", color: "var(--color-warn)" }}
        >
          <p className="font-semibold">{report.summary}</p>
          <ul className="mt-1 list-disc pl-5 text-[color:var(--color-ink-soft)]">
            {[...(report.errors ?? []), ...(report.warnings ?? [])]
              .slice(0, 5)
              .map((line, index) => (
                <li key={index}>{line}</li>
              ))}
          </ul>
        </div>
      )}

      <Tabs
        tabs={[
          { id: "edit", label: "Edit" },
          { id: "export", label: "Export", badge: data.files.length },
          { id: "details", label: "Details" },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-5">
        {tab === "edit" && (
          <>
            {dirty && (
              <div
                className="sticky top-16 z-30 mb-3 rounded-lg px-3.5 py-2.5 flex items-center gap-3 text-sm shadow-sm"
                style={{ background: "var(--color-brand-soft)", color: "var(--color-brand)" }}
              >
                <span className="flex-1">Unsaved changes.</span>
                <button
                  className="btn"
                  onClick={() => setDraft(structuredClone(data.content))}
                  disabled={save.isPending}
                >
                  Discard
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => save.mutate()}
                  disabled={save.isPending}
                >
                  {save.isPending ? <Spinner /> : null}
                  Save
                </button>
              </div>
            )}
            {save.error && <ErrorBanner error={save.error} />}
            {draft && <Editor kind={data.kind} content={draft} onChange={setDraft} />}
          </>
        )}

        {tab === "export" && (
          <ExportPanel
            artifactId={id}
            files={data.files}
            formats={formats.data ?? []}
            version={data.version}
            onExport={(keys, force) => exportMutation.mutate({ formats: keys, force })}
            pending={exportMutation.isPending}
            error={exportMutation.error}
          />
        )}

        {tab === "details" && (
          <Card>
            <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-2.5 text-sm">
              <Detail label="Type" value={data.kind.replace(/_/g, " ")} />
              <Detail label="Model" value={data.model_used || "—"} />
              <Detail
                label="Generation time"
                value={data.generation_seconds ? `${data.generation_seconds}s` : "—"}
              />
              <Detail label="Version" value={String(data.version)} />
              <Detail label="Created" value={new Date(data.created_at).toLocaleString()} />
              <Detail label="Updated" value={new Date(data.updated_at).toLocaleString()} />
            </dl>
            {data.source_document_ids.length > 0 && (
              <div className="mt-4">
                <span className="label">Built from</span>
                <div className="flex flex-wrap gap-1.5">
                  {data.source_document_ids.map((documentId) => (
                    <Link key={documentId} to={`/documents/${documentId}`} className="chip">
                      {documentId.slice(0, 8)}
                    </Link>
                  ))}
                </div>
              </div>
            )}
            <details className="mt-4">
              <summary className="cursor-pointer text-sm font-medium">
                The request that produced this
              </summary>
              <pre className="mt-2 text-xs overflow-x-auto p-3 rounded bg-[color:var(--color-surface)]">
                {JSON.stringify(data.params, null, 2)}
              </pre>
            </details>
          </Card>
        )}
      </div>

      <Modal open={adaptOpen} onClose={() => setAdaptOpen(false)} title="Adapt this">
        <AdaptForm
          busy={adaptJobId !== null && !adaptJob.done}
          progress={adaptJob}
          onSubmit={async (body) => {
            const result = await api.adapt(id, body);
            setAdaptJobId(result.job_id);
          }}
        />
      </Modal>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-[color:var(--color-line)] pb-1.5">
      <dt className="text-[color:var(--color-ink-soft)]">{label}</dt>
      <dd className="font-medium text-right">{value}</dd>
    </div>
  );
}

function Editor({
  kind,
  content,
  onChange,
}: {
  kind: string;
  content: any;
  onChange: (next: any) => void;
}) {
  switch (kind) {
    case "slides":
      return <DeckEditor content={content} onChange={onChange} />;
    case "notes":
      return <NotesEditor content={content} onChange={onChange} />;
    case "exam":
      return <PaperEditor content={content} onChange={onChange} />;
    default:
      return <JsonEditor content={content} onChange={onChange} />;
  }
}

function ExportPanel({
  artifactId,
  files,
  formats,
  version,
  onExport,
  pending,
  error,
}: {
  artifactId: string;
  files: ArtifactFile[];
  formats: { key: string; label: string; description: string; available: boolean; install: string }[];
  version: number;
  onExport: (formats: string[], force?: boolean) => void;
  pending: boolean;
  error: unknown;
}) {
  const byFormat = new Map<string, ArtifactFile[]>();
  for (const file of files) {
    byFormat.set(file.fmt, [...(byFormat.get(file.fmt) ?? []), file]);
  }
  const anyStale = files.some((file) => file.source_version !== version);

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner error={error} /> : null}

      {anyStale && (
        <div
          className="rounded-lg px-3.5 py-2.5 text-sm flex items-center gap-3"
          style={{ background: "var(--color-warn-soft)", color: "var(--color-warn)" }}
        >
          <span className="flex-1">
            You have edited this since these files were made. Re-export to bring them up to date.
          </span>
          <button
            className="btn"
            disabled={pending}
            onClick={() => onExport([...byFormat.keys()], true)}
          >
            Re-export all
          </button>
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        {formats.map((format) => {
          const existing = byFormat.get(format.key) ?? [];
          return (
            <Card key={format.key} className="flex flex-col gap-2">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-sm">{format.label}</div>
                  <div className="text-xs text-[color:var(--color-ink-soft)]">
                    {format.description}
                  </div>
                </div>
                <button
                  className="btn !py-1 !px-2.5 text-xs shrink-0"
                  disabled={!format.available || pending}
                  onClick={() => onExport([format.key], existing.length > 0)}
                >
                  {pending ? <Spinner size={12} /> : existing.length ? "Refresh" : "Create"}
                </button>
              </div>

              {!format.available && (
                <p className="text-xs text-[color:var(--color-ink-faint)]">
                  Needs <code className="text-[11px]">{format.install}</code>
                </p>
              )}

              {existing.map((file) => (
                <a
                  key={file.id}
                  href={api.downloadUrl(artifactId, file.id)}
                  download
                  className="flex items-center gap-2 text-sm rounded px-2 py-1.5 hover:bg-[color:var(--color-surface-2)] transition-colors"
                >
                  <span aria-hidden="true">⬇</span>
                  <span className="flex-1 truncate">
                    {file.role === "answer_key" ? "Answer key" : file.filename}
                  </span>
                  <span className="text-xs text-[color:var(--color-ink-faint)] tabular-nums shrink-0">
                    {formatBytes(file.size_bytes)}
                  </span>
                  {file.source_version !== version && (
                    <span className="chip" style={{ color: "var(--color-warn)" }}>
                      old
                    </span>
                  )}
                </a>
              ))}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function AdaptForm({
  onSubmit,
  busy,
  progress,
}: {
  onSubmit: (body: Record<string, unknown>) => void;
  busy: boolean;
  progress: { progress: number; message: string; error: string };
}) {
  const [readingLevel, setReadingLevel] = useState("");
  const [language, setLanguage] = useState("");
  const [variant, setVariant] = useState("");
  const [scaffolding, setScaffolding] = useState(false);

  const nothingChosen = !readingLevel && !language && !variant && !scaffolding;

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({
          target_reading_level: readingLevel,
          target_language: language,
          variant,
          add_scaffolding: scaffolding,
        });
      }}
    >
      <p className="text-sm text-[color:var(--color-ink-soft)]">
        Makes a new copy with the same structure and the same content, changed only in the ways
        you pick. The original is untouched.
      </p>

      <Field label="Reading level" hint="Rewrites the language without dropping any content.">
        <select
          className="field"
          value={readingLevel}
          onChange={(event) => setReadingLevel(event.target.value)}
        >
          <option value="">Leave as it is</option>
          <option value="Grade 3">Grade 3</option>
          <option value="Grade 5">Grade 5</option>
          <option value="Grade 7">Grade 7</option>
          <option value="Grade 9">Grade 9</option>
          <option value="Grade 11">Grade 11</option>
        </select>
      </Field>

      <Field label="Translate into" hint="Subject terms keep the original in brackets.">
        <input
          className="field"
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
          placeholder="Urdu, Spanish, French…"
        />
      </Field>

      <Field label="Differentiated version">
        <select className="field" value={variant} onChange={(event) => setVariant(event.target.value)}>
          <option value="">None</option>
          <option value="support">Support — same content, more scaffolding</option>
          <option value="core">Core</option>
          <option value="extension">Extension — same content, higher demand</option>
        </select>
      </Field>

      <Toggle
        checked={scaffolding}
        onChange={setScaffolding}
        label="Add scaffolding"
        hint="Sentence starters, a worked first step, a word bank."
      />

      {busy && <ProgressBar value={progress.progress} label={progress.message || "Adapting…"} />}
      {progress.error && <ErrorBanner error={new Error(progress.error)} />}

      <button className="btn btn-primary" type="submit" disabled={busy || nothingChosen}>
        {busy ? <Spinner /> : null}
        {busy ? "Adapting…" : "Create the adapted copy"}
      </button>
    </form>
  );
}
