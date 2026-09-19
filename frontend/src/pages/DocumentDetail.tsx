import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Card, ErrorBanner, Loading, PageHeader, StatusBadge, Tabs } from "../components/ui";
import { api, formatBytes } from "../lib/api";

export default function DocumentDetail() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState("text");

  const document = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id) });
  const markdown = useQuery({
    queryKey: ["document-markdown", id],
    queryFn: () => api.documentMarkdown(id),
    enabled: tab === "text" && document.data?.status === "ready",
    retry: false,
  });
  const chunks = useQuery({
    queryKey: ["document-chunks", id],
    queryFn: () => api.documentChunks(id, 100),
    enabled: tab === "passages",
  });

  if (document.isLoading) return <Loading />;
  if (document.error) return <ErrorBanner error={document.error} onRetry={document.refetch} />;
  if (!document.data) return null;

  const data = document.data;

  return (
    <div>
      <PageHeader
        title={data.title || data.original_name}
        subtitle={[
          formatBytes(data.size_bytes),
          data.page_count ? `${data.page_count} pages` : "",
          data.word_count ? `${data.word_count.toLocaleString()} words` : "",
          data.chunk_count ? `${data.chunk_count} passages` : "",
          data.used_ocr ? "read with OCR" : "",
        ]
          .filter(Boolean)
          .join(" · ")}
        actions={
          <>
            <StatusBadge status={data.status} />
            <Link className="btn btn-primary" to="/generate">
              ✨ Make something from this
            </Link>
          </>
        }
      />

      {data.error && <ErrorBanner error={new Error(data.error)} />}

      <Tabs
        tabs={[
          { id: "text", label: "Extracted text" },
          { id: "passages", label: "Passages", badge: data.chunk_count },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-5">
        {tab === "text" &&
          (markdown.isLoading ? (
            <Loading label="Reading" />
          ) : markdown.error ? (
            <ErrorBanner error={markdown.error} />
          ) : (
            <Card>
              <p className="text-xs text-[color:var(--color-ink-faint)] mb-3">
                This is exactly what the model sees. If something is garbled here, the source was
                hard to read — try re-adding it with OCR enabled in Settings.
              </p>
              <pre className="whitespace-pre-wrap text-[13px] leading-relaxed max-h-[70vh] overflow-y-auto font-mono">
                {markdown.data?.markdown}
              </pre>
            </Card>
          ))}

        {tab === "passages" &&
          (chunks.isLoading ? (
            <Loading />
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-[color:var(--color-ink-faint)]">
                The document split into retrievable pieces. Each keeps its heading path and page,
                which is what lets a generated question cite where it came from.
              </p>
              {(chunks.data ?? []).map((chunk) => (
                <Card key={chunk.id}>
                  <div className="flex flex-wrap gap-2 text-xs text-[color:var(--color-ink-faint)] mb-1.5">
                    <span className="tabular-nums">#{chunk.ordinal + 1}</span>
                    {chunk.section_path && <span>{chunk.section_path}</span>}
                    {chunk.page_from && <span>p.{chunk.page_from}</span>}
                    <span className="ml-auto tabular-nums">~{chunk.token_count} tokens</span>
                  </div>
                  <p className="text-sm whitespace-pre-wrap">{chunk.text}</p>
                </Card>
              ))}
            </div>
          ))}
      </div>
    </div>
  );
}
