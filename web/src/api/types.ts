/** A端数据的前端类型。D1 先手工声明核心形状；`npm run gen:api` 生成的完整类型见 a-schema.d.ts。 */

export interface BookManifest {
  book_id: string
  title: string
  is_mock: boolean
  revision: number
  pages: PageInfo[]
  entities?: EntityInfo[]
  events?: EventInfo[]
}

export interface PageInfo {
  page_id: string
  page_revision: number
  sequence: number
  printed_page?: string
  image_asset_id?: string
  ocr?: { lines?: { text: string; confidence?: number; reading_order?: number }[] }
  facts?: { fact_id: string; text: string; source_refs?: string[] }[]
}

export interface EntityInfo {
  entity_id: string
  name: string
  aliases?: string[]
  page_ids?: string[]
}

export interface EventInfo {
  event_id: string
  page_id: string
  summary: string
  order?: number
}

export interface ClipRow {
  clip_id: string
  page_id: string
  shot_id?: string
  asset_version: number
  duration_ms: number
  status: 'approved' | 'needs_review' | 'failed' | 'expired' | 'fixture'
  is_mock: boolean
  asset_id?: string
  audio_asset_id?: string
  subtitle_asset_id?: string
  motion_status?: string
  frame_evidence?: FrameEvidenceRow[]
}

export interface FrameEvidenceRow {
  f_id: string
  t_ms: number
  image_asset_id: string
  claims: { claim: string; region?: { x1: number; y1: number; x2: number; y2: number } }[]
}
