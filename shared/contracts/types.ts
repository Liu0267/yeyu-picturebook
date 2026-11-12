// Generated from Pydantic contracts by scripts/export_contracts.py.
// Times: milliseconds. Regions: normalized top-left x1/y1/x2/y2.

export type BookContentReview = {
  expected_revision: number;
  reviewer: string;
  source_refs: Array<SourceRef>;
  facts: Array<Fact>;
  entities: Array<Entity>;
  events: Array<Event>;
  reviewed_page_ids?: Array<string>;
};

export type BookManifest = {
  schema_version?: string;
  book_id: string;
  title: string;
  revision?: number;
  is_mock?: boolean;
  rights_status?: string;
  external_upload_allowed?: boolean;
  source_filename: string;
  source_sha256: string;
  pages: Array<Page>;
  source_refs?: Array<SourceRef>;
  facts?: Array<Fact>;
  entities?: Array<Entity>;
  events?: Array<Event>;
};

export type ClipAsset = {
  clip_id: string;
  book_id: string;
  page_id: string;
  shot_id: string;
  manifest_revision: number;
  page_revision: number;
  asset_version: number;
  duration_ms: number;
  status: "needs_review" | "approved" | "failed" | "expired";
  asset_id: string;
  is_mock: boolean;
  provenance: "synthetic_mock" | "model_generated";
  motion_status?: "not_assessed" | "passed" | "failed" | "not_required";
  run_id: string;
  model_call_id?: string | null;
  reviewed_by?: string | null;
  audio_asset_id?: string | null;
  subtitle_asset_id?: string | null;
  source_video_asset_id?: string | null;
};

export type ClipReview = {
  expected_asset_version: number;
  reviewer: string;
  claims_by_frame: Record<string, Array<VisualClaim>>;
  audit_resolution_notes?: Record<string, string>;
  motion_review?: "not_assessed" | "passed" | "failed";
  motion_notes?: string;
};

export type CompilationAsset = {
  compilation_id: string;
  book_id: string;
  asset_version: number;
  asset_id: string;
  subtitle_asset_id: string;
  duration_ms: number;
  chapters: Array<Record<string, unknown>>;
  status: "needs_review" | "approved" | "expired" | "failed";
  run_id: string;
  is_mock: boolean;
  reviewed_by?: string | null;
};

export type CompilationCreate = {
  items: Array<CompilationItem>;
  idempotency_key: string;
};

export type CompilationItem = {
  clip_id: string;
  asset_version: number;
};

export type CompilationReview = {
  expected_asset_version: number;
  reviewer: string;
  claims_by_frame: Record<string, Array<VisualClaim>>;
};

export type Entity = {
  entity_id: string;
  name: string;
  description: string;
  source_ref_ids: Array<string>;
};

export type ErrorInfo = {
  code: string;
  message: string;
  retryable?: boolean;
};

export type Event = {
  event_id: string;
  sequence: number;
  description: string;
  fact_ids: Array<string>;
  entity_ids?: Array<string>;
};

export type Fact = {
  fact_id: string;
  page_id: string;
  claim: string;
  kind: "text_explicit" | "visual_observation" | "inference";
  source_ref_ids: Array<string>;
  status?: "draft" | "reviewed";
  reviewed_by?: string | null;
};

export type FrameEvidence = {
  frame_id: string;
  clip_id: string;
  asset_version: number;
  t_ms: number;
  image_asset_id: string;
  video_sha256: string;
  visual_claims?: Array<VisualClaim>;
  status?: "needs_review" | "reviewed" | "expired";
  is_mock: boolean;
};

export type NarrationAsset = {
  narration_id: string;
  book_id: string;
  page_id: string;
  page_revision: number;
  shot_id: string;
  run_id: string;
  model_id: string;
  audio_asset_id: string;
  text: string;
  speaker: string;
  duration_ms: number;
  sample_rate: number;
  status?: "needs_review" | "expired";
  is_mock?: boolean;
};

export type OCRLine = {
  line_id: string;
  raw_text: string;
  confidence?: number | null;
  region: Region;
  suggested_text?: string | null;
  confirmed_text?: string | null;
};

export type ObservationReview = {
  expected_page_revision: number;
  reviewer: string;
  decision: "corrected" | "rejected";
  corrected_observation?: PageObservation | null;
  notes: string;
};

export type Page = {
  page_id: string;
  sequence: number;
  printed_page?: string | null;
  page_revision?: number;
  image_asset_id: string;
  width: number;
  height: number;
  source_locator: string;
  ocr_engine: string;
  ocr_lines?: Array<OCRLine>;
  quality_flags?: Array<string>;
  review_status?: "needs_review" | "reviewed";
};

export type PageObservation = {
  visible_objects: Array<VisibleObject>;
  explicit_text: string;
  visual_summary: string;
  uncertainties: Array<string>;
};

export type PipelineCreate = {
  shot_id: string;
  idempotency_key: string;
  resume_run_ids?: Record<string, string>;
};

export type PreprocessCreate = {
  expected_page_revision: number;
  rotation_clockwise?: 0 | 90 | 180 | 270;
  chinese_text_region?: Region | null;
  corners_after_rotation?: Array<[number, number]> | null;
};

export type Region = {
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
};

export type RunRecord = {
  run_id: string;
  step: string;
  model_version?: string | null;
  input_hash: string;
  status: "queued" | "running" | "needs_review" | "succeeded" | "failed" | "cancelled" | "expired";
  error?: ErrorInfo | null;
  attempts?: number;
  max_attempts?: number;
  created_at: string;
  updated_at: string;
  payload: Record<string, unknown>;
  outputs?: Array<string>;
  trace?: Array<Record<string, unknown>>;
  usage?: Record<string, unknown>;
  is_mock?: boolean;
};

export type ShotSpec = {
  shot_id: string;
  book_id: string;
  manifest_revision: number;
  page_id: string;
  page_revision: number;
  fact_ids: Array<string>;
  entity_ids?: Array<string>;
  reference_asset_id: string;
  end_keyframe_asset_id?: string | null;
  reference_region: Region;
  narration: string;
  creative_additions?: Array<string>;
  prompt_version?: string;
  prompt: string;
  negative_prompt: string;
  target_duration_ms: number;
  status?: "needs_review" | "approved";
};

export type SourceRef = {
  ref_id: string;
  page_id: string;
  kind: "original_text" | "original_image";
  locator: string;
  region: Region;
  excerpt?: string;
};

export type VisibleObject = {
  name: string;
  description: string;
  bbox: [number, number, number, number];
};

export type VisualClaim = {
  claim: string;
  region: Region;
  observed_by: string;
  method: "human_frame_review" | "vlm_frame_review";
};

export type APIEnvelope<T> = { request_id: string; status: "ok" | "accepted" | "error"; data: T | null; error: ErrorInfo | null; warnings: string[] };
