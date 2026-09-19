/** Shapes returned by the API. Kept narrow — only what the UI actually reads. */

export type ArtifactKind =
  | "slides"
  | "notes"
  | "exam"
  | "lesson_plan"
  | "rubric"
  | "worksheet"
  | "flashcards"
  | "grading";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type DocumentStatus = "uploaded" | "parsing" | "indexing" | "ready" | "failed";

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  progress: number;
  message: string;
  error: string;
  result: Record<string, unknown>;
  document_id: string | null;
  artifact_id: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface JobEvent {
  job_id: string;
  type: "snapshot" | "queued" | "started" | "progress" | "succeeded" | "failed" | "cancelled" | "ping";
  status?: JobStatus;
  progress?: number;
  message?: string;
  error?: string;
  result?: Record<string, unknown>;
}

export interface Doc {
  id: string;
  original_name: string;
  title: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error: string;
  page_count: number;
  word_count: number;
  chunk_count: number;
  used_ocr: boolean;
  subject: string;
  grade_level: string;
  tags: string[];
  course_id: string | null;
  created_at: string;
}

export interface Chunk {
  id: string;
  ordinal: number;
  text: string;
  heading: string;
  section_path: string;
  page_from: number | null;
  token_count: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  text: string;
  score: number;
  page: number | null;
  section: string;
}

export interface ArtifactFile {
  id: string;
  fmt: string;
  role: string;
  filename: string;
  size_bytes: number;
  source_version: number;
  created_at: string;
  stale: boolean;
}

export interface AnswerDisagreement {
  number: number;
  question: string;
  key_answer: string;
  independent_answer: string;
  reason: string;
  confidence: number;
}

/** The quality audits attached to a generated artifact. */
export interface ArtifactReport {
  // blueprint
  blueprint_matches?: boolean;
  actual_marks?: number;
  planned_marks?: number;
  errors?: string[];
  warnings?: string[];
  summary?: string;
  bloom_actual?: Record<string, number>;
  bloom_planned?: Record<string, number>;
  topic_actual?: Record<string, number>;
  topic_planned?: Record<string, number>;
  // independent answer-key check
  answers_checked?: number;
  answers_agreed?: number;
  answers_ok?: boolean;
  answers_summary?: string;
  answer_disagreements?: AnswerDisagreement[];
}

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  title: string;
  report: ArtifactReport;
  status: "draft" | "generating" | "ready" | "failed";
  error: string;
  params: Record<string, unknown>;
  source_document_ids: string[];
  course_id: string | null;
  model_used: string;
  generation_seconds: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactDetail extends Artifact {
  content: Record<string, any>;
  files: ArtifactFile[];
}

export interface FormatInfo {
  key: string;
  label: string;
  extension: string;
  description: string;
  available: boolean;
  install: string;
  supports_answer_key: boolean;
}

export interface KindInfo {
  kind: ArtifactKind;
  label: string;
  description: string;
  request_schema: Record<string, any>;
  content_schema: Record<string, any>;
  formats: FormatInfo[];
}

export interface Theme {
  key: string;
  name: string;
  description: string;
  accent: string;
  background: string;
  dark: string;
}

export interface Capability {
  label: string;
  available: boolean;
  enables: string;
  install: string;
}

/** How the loaded model is split between GPU and CPU, straight from Ollama. */
export interface ModelPlacement {
  loaded: boolean;
  model?: string;
  total_gb?: number;
  vram_gb?: number;
  gpu_share?: number;
  fully_on_gpu?: boolean;
}

export interface HardwareReport {
  summary: string;
  has_gpu: boolean;
  gpu_name: string;
  vram_total_mb: number;
  vram_free_mb: number;
  embeddings_device: string;
  torch_cuda: boolean;
}

export interface StatusReport {
  database: boolean;
  llm: {
    provider: string;
    model: string;
    base_url: string;
    context?: number;
    auto_context?: boolean;
    context_reason?: string;
    placement?: ModelPlacement;
  };
  hardware?: HardwareReport;
  capabilities: Record<string, Capability>;
}

export interface ProbeResult {
  ok: boolean;
  provider: string;
  base_url: string;
  model: string;
  message: string;
  models_available: string[];
  latency_ms: number | null;
}

export interface SettingsPayload {
  values: Record<string, any>;
  overridden: string[];
  editable: string[];
}

export interface BankQuestion {
  id: string;
  qtype: string;
  topic: string;
  bloom: string;
  difficulty: string;
  marks: number;
  stem: string;
  payload: Record<string, any>;
  times_used: number;
  created_at: string;
}

export interface Course {
  id: string;
  name: string;
  subject: string;
  grade_level: string;
  description: string;
  outcomes: string[];
  created_at: string;
}

export interface Coverage {
  course_id: string;
  artifacts: number;
  questions: number;
  outcomes: { outcome: string; covered: boolean; match_count: number; matches: string[] }[];
  uncovered: string[];
  marks_by_bloom: Record<string, number>;
}

/** The blueprint audit returned alongside a generated exam. */
export interface BlueprintReport {
  blueprint_matches: boolean;
  actual_marks: number;
  planned_marks: number;
  errors: string[];
  warnings: string[];
  bloom_actual: Record<string, number>;
  bloom_planned: Record<string, number>;
  topic_actual: Record<string, number>;
  topic_planned: Record<string, number>;
  summary: string;
}
