import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  PageHeader,
  ProgressBar,
  Spinner,
  StatusBadge,
} from "../components/ui";
import { api, formatBytes } from "../lib/api";
import type { SearchHit } from "../lib/types";
import { useJob } from "../lib/useJob";

const ACCEPTED =
  ".pdf,.docx,.pptx,.xlsx,.html,.htm,.md,.markdown,.txt,.rst,.csv,.tsv,.json,.png,.jpg,.jpeg,.tiff,.bmp,.webp";

export default function Documents() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [subject, setSubject] = useState("");
  const [grade, setGrade] = useState("");
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.documents({}),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => ["uploaded", "parsing", "indexing"].includes(d.status))
        ? 2000
        : false,
  });

  const stats = useQuery({ queryKey: ["library-stats"], queryFn: api.libraryStats });

  const job = useJob(activeJob, () => {
    setActiveJob(null);
    queryClient.invalidateQueries({ queryKey: ["documents"] });
    queryClient.invalidateQueries({ queryKey: ["library-stats"] });
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const results = [];
      for (const file of files) {
        results.push(await api.uploadDocument(file, { subject, grade_level: grade }));
      }
      return results;
    },
    onSuccess: (results) => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      const lastJob = results.reverse().find((r) => r.job_id)?.job_id;
      if (lastJob) setActiveJob(lastJob);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["library-stats"] });
    },
  });

  const runSearch = useMutation({
    mutationFn: () => api.searchDocuments(search, 8),
    onSuccess: setHits,
  });

  const handleFiles = (list: FileList | null) => {
    if (list && list.length) upload.mutate(Array.from(list));
  };

  const duplicates = (upload.data ?? []).filter((r) => r.duplicate_of);

  return (
    <div>
      <PageHeader
        title="Sources"
        subtitle="Add your chapters, syllabus and handouts. Everything generated from them is cited back to the page."
      />

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          handleFiles(event.dataTransfer.files);
        }}
        className="card p-8 text-center mb-5 transition-colors"
        style={{
          borderStyle: "dashed",
          borderWidth: 2,
          borderColor: dragging ? "var(--color-brand)" : "var(--color-line)",
          background: dragging ? "var(--color-brand-soft)" : "var(--color-paper)",
        }}
      >
        <div className="text-2xl mb-2" aria-hidden="true">
          📄
        </div>
        <p className="font-medium">Drop files here, or</p>
        <button
          className="btn btn-primary mt-2"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          type="button"
        >
          {upload.isPending ? <Spinner /> : null}
          {upload.isPending ? "Uploading…" : "Choose files"}
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ACCEPTED}
          className="hidden"
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <p className="text-xs text-[color:var(--color-ink-faint)] mt-3">
          PDF, Word, PowerPoint, Excel, Markdown, HTML, CSV and images. Scanned PDFs are read with
          OCR.
        </p>

        <div className="flex gap-2 justify-center mt-4 max-w-md mx-auto">
          <input
            className="field"
            placeholder="Subject (optional)"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
          <input
            className="field"
            placeholder="Class (optional)"
            value={grade}
            onChange={(event) => setGrade(event.target.value)}
          />
        </div>
      </div>

      {upload.error && <ErrorBanner error={upload.error} />}

      {duplicates.length > 0 && (
        <div
          className="rounded-lg px-3.5 py-2.5 text-sm mb-4"
          style={{ background: "var(--color-surface)", color: "var(--color-ink-soft)" }}
        >
          {duplicates.length} file{duplicates.length > 1 ? "s were" : " was"} already in your
          library, so nothing was processed twice.
        </div>
      )}

      {activeJob && !job.done && (
        <Card className="mb-4">
          <ProgressBar value={job.progress} label={job.message || "Processing…"} />
        </Card>
      )}

      {(stats.data?.documents ?? 0) > 0 && (
        <Card className="mb-5">
          <div className="flex gap-2">
            <input
              className="field"
              placeholder="Search everything you have added…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && search.trim().length > 1) runSearch.mutate();
              }}
            />
            <button
              className="btn"
              onClick={() => runSearch.mutate()}
              disabled={search.trim().length < 2 || runSearch.isPending}
              type="button"
            >
              {runSearch.isPending ? <Spinner /> : "Search"}
            </button>
          </div>
          {runSearch.error && (
            <div className="mt-3">
              <ErrorBanner error={runSearch.error} />
            </div>
          )}
          {hits && (
            <div className="mt-3 space-y-2">
              {hits.length === 0 ? (
                <p className="text-sm text-[color:var(--color-ink-soft)]">Nothing matched.</p>
              ) : (
                hits.map((hit) => (
                  <div
                    key={hit.chunk_id}
                    className="rounded-md p-3 text-sm"
                    style={{ background: "var(--color-surface)" }}
                  >
                    <div className="flex flex-wrap gap-2 text-xs text-[color:var(--color-ink-faint)] mb-1">
                      <Link to={`/documents/${hit.document_id}`} className="font-medium">
                        {hit.document_title || "source"}
                      </Link>
                      {hit.section && <span>{hit.section}</span>}
                      {hit.page && <span>p.{hit.page}</span>}
                      <span className="ml-auto tabular-nums">{hit.score.toFixed(3)}</span>
                    </div>
                    <p className="line-clamp-4">{hit.text}</p>
                  </div>
                ))
              )}
            </div>
          )}
        </Card>
      )}

      {documents.isLoading ? (
        <Loading />
      ) : (documents.data?.length ?? 0) === 0 ? (
        <Card padded={false}>
          <EmptyState
            icon="📂"
            title="No sources yet"
            body="You can generate from a topic alone, but adding your own material makes the output match what you actually teach — and every claim gets cited."
          />
        </Card>
      ) : (
        <ul className="space-y-2">
          {documents.data!.map((document) => (
            <li key={document.id} className="card px-4 py-3 flex items-center gap-3">
              <Link to={`/documents/${document.id}`} className="min-w-0 flex-1">
                <div className="font-medium text-sm truncate">
                  {document.title || document.original_name}
                </div>
                <div className="text-xs text-[color:var(--color-ink-faint)] flex flex-wrap gap-x-3">
                  <span>{formatBytes(document.size_bytes)}</span>
                  {document.page_count > 0 && <span>{document.page_count} pages</span>}
                  {document.word_count > 0 && (
                    <span>{document.word_count.toLocaleString()} words</span>
                  )}
                  {document.chunk_count > 0 && <span>{document.chunk_count} passages</span>}
                  {document.used_ocr && <span>OCR</span>}
                  {document.subject && <span>{document.subject}</span>}
                </div>
                {document.error && (
                  <div className="text-xs mt-1" style={{ color: "var(--color-danger)" }}>
                    {document.error}
                  </div>
                )}
              </Link>
              <StatusBadge status={document.status} />
              <button
                className="btn btn-danger !py-1 !px-2 text-xs"
                type="button"
                onClick={() => {
                  if (confirm(`Remove ${document.title || document.original_name}?`))
                    remove.mutate(document.id);
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
