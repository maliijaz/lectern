/** Typed API client.
 *
 * One place that knows how to talk to the backend, including how to unwrap the
 * `{error: {code, message}}` envelope the server uses — so every screen shows the
 * server's own explanation rather than "Request failed".
 */

import type {
  Artifact,
  ArtifactDetail,
  ArtifactFile,
  ArtifactKind,
  BankQuestion,
  Chunk,
  Course,
  Coverage,
  Doc,
  FormatInfo,
  Job,
  KindInfo,
  ProbeResult,
  SearchHit,
  SettingsPayload,
  StatusReport,
  Theme,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = "error",
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Install hint the server attaches when an optional dependency is missing. */
  get install(): string {
    return typeof this.detail.install === "string" ? this.detail.install : "";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      "Cannot reach the server. Is the backend running on port 8000?",
      0,
      "network",
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let body: any = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    const envelope = body?.error;
    if (envelope) {
      const { code, message, ...detail } = envelope;
      throw new ApiError(message ?? "Request failed", response.status, code, detail);
    }
    // FastAPI's own validation errors come through as `detail`.
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: any) => `${d.loc?.join(".")}: ${d.msg}`).join("; ")
          : `Request failed (${response.status})`;
    throw new ApiError(message, response.status);
  }

  return body as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = (path: string) => request<void>(path, { method: "DELETE" });

const query = (params: Record<string, string | number | boolean | undefined | null>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
};

export const api = {
  // ---------------------------------------------------------------- meta
  status: () => get<StatusReport>("/settings/status"),
  settings: () => get<SettingsPayload>("/settings"),
  updateSettings: (values: Record<string, unknown>) =>
    put<SettingsPayload>("/settings", { values }),
  resetSettings: () => post<SettingsPayload>("/settings/reset"),
  probe: () => post<ProbeResult>("/settings/probe"),

  // ---------------------------------------------------------------- documents
  documents: (params: { status?: string; q?: string; course_id?: string } = {}) =>
    get<Doc[]>(`/documents${query(params)}`),
  document: (id: string) => get<Doc>(`/documents/${id}`),
  documentMarkdown: (id: string) =>
    get<{ markdown: string; total_chars: number }>(`/documents/${id}/markdown`),
  documentChunks: (id: string, limit = 50) =>
    get<Chunk[]>(`/documents/${id}/chunks${query({ limit })}`),
  libraryStats: () =>
    get<{ documents: number; words: number; chunks: number; ingesting: number }>(
      "/documents/stats/summary",
    ),
  searchDocuments: (q: string, topK = 10) =>
    get<SearchHit[]>(`/documents/search/query${query({ q, top_k: topK })}`),
  uploadDocument: (file: File, meta: { subject?: string; grade_level?: string; course_id?: string }) => {
    const form = new FormData();
    form.append("file", file);
    if (meta.subject) form.append("subject", meta.subject);
    if (meta.grade_level) form.append("grade_level", meta.grade_level);
    if (meta.course_id) form.append("course_id", meta.course_id);
    return request<{ document: Doc; job_id: string | null; duplicate_of: string | null }>(
      "/documents",
      { method: "POST", body: form },
    );
  },
  updateDocument: (id: string, body: Partial<Doc>) => patch<Doc>(`/documents/${id}`, body),
  reingestDocument: (id: string) => post<{ job_id: string }>(`/documents/${id}/reingest`),
  deleteDocument: (id: string) => del(`/documents/${id}`),

  // ---------------------------------------------------------------- artifacts
  kinds: () => get<KindInfo[]>("/artifacts/kinds"),
  themes: () => get<Theme[]>("/artifacts/themes"),
  artifacts: (params: { kind?: string; status?: string; q?: string; course_id?: string } = {}) =>
    get<Artifact[]>(`/artifacts${query(params)}`),
  artifact: (id: string) => get<ArtifactDetail>(`/artifacts/${id}`),
  generate: (kind: ArtifactKind, params: Record<string, unknown>, exportFormats?: string[]) =>
    post<{ artifact: Artifact; job_id: string }>("/artifacts", {
      kind,
      params,
      export_formats: exportFormats ?? null,
    }),
  regenerate: (id: string, overrides?: Record<string, unknown>) =>
    post<{ artifact: Artifact; job_id: string }>(`/artifacts/${id}/regenerate`, overrides ?? {}),
  adapt: (id: string, body: Record<string, unknown>) =>
    post<{ job_id: string }>(`/artifacts/${id}/adapt`, body),
  updateContent: (id: string, content: Record<string, unknown>, title?: string) =>
    put<ArtifactDetail>(`/artifacts/${id}/content`, { content, title }),
  deleteArtifact: (id: string) => del(`/artifacts/${id}`),
  formats: (id: string) => get<FormatInfo[]>(`/artifacts/${id}/formats`),
  exportArtifact: (
    id: string,
    formats: string[],
    opts: { theme?: string; include_answer_key?: boolean; force?: boolean } = {},
  ) => post<ArtifactFile[]>(`/artifacts/${id}/export`, { formats, ...opts }),
  downloadUrl: (artifactId: string, fileId: string) =>
    `${BASE}/artifacts/${artifactId}/files/${fileId}/download`,

  // ---------------------------------------------------------------- jobs
  job: (id: string) => get<Job>(`/jobs/${id}`),
  jobs: (params: { status?: string; kind?: string; limit?: number } = {}) =>
    get<Job[]>(`/jobs${query(params)}`),
  cancelJob: (id: string) => post<Job>(`/jobs/${id}/cancel`),
  jobStreamUrl: (id: string) => `${BASE}/jobs/${id}/stream`,

  // ---------------------------------------------------------------- question bank
  bank: (
    params: {
      q?: string;
      qtype?: string;
      bloom?: string;
      difficulty?: string;
      topic?: string;
      limit?: number;
    } = {},
  ) => get<BankQuestion[]>(`/question-bank${query(params)}`),
  bankFacets: () =>
    get<{
      total: number;
      topics: Record<string, number>;
      types: Record<string, number>;
      bloom: Record<string, number>;
      difficulty: Record<string, number>;
    }>("/question-bank/facets"),
  deleteBankQuestion: (id: string) => del(`/question-bank/${id}`),

  // ---------------------------------------------------------------- courses
  courses: () => get<Course[]>("/courses"),
  createCourse: (body: Partial<Course>) => post<Course>("/courses", body),
  updateCourse: (id: string, body: Partial<Course>) => patch<Course>(`/courses/${id}`, body),
  deleteCourse: (id: string) => del(`/courses/${id}`),
  coverage: (id: string) => get<Coverage>(`/courses/${id}/coverage`),
};

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
