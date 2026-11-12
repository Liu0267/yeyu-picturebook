/** OCR校对页：左侧原页图，右侧OCR行编辑；保存PATCH /aapi/v1/pages/{id}/review（带期望版本）。 */
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { BookManifest } from '../api/types'

interface OcrLine { line_id: string; raw_text: string; confidence: number }
interface MPage {
  page_id: string; page_revision: number; image_asset_id?: string
  printed_page?: string; ocr_lines?: OcrLine[]; review_status?: string
}

export default function ReviewPage() {
  const { bookId } = useParams()
  const [pages, setPages] = useState<MPage[]>([])
  const [sel, setSel] = useState<string>('')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [title, setTitle] = useState('')

  useEffect(() => {
    if (!bookId) return
    api<BookManifest>(`/v1/books/${bookId}/manifest`).then((m) => {
      setTitle(m.title)
      setPages(m.pages as unknown as MPage[])
      setSel(m.pages?.[0]?.page_id ?? '')
    }).catch((e) => setMsg(e.message))
  }, [bookId])

  const page = pages.find((p) => p.page_id === sel)
  const lowConf = (page?.ocr_lines ?? []).filter((l) => l.confidence < 0.6).length

  async function save() {
    if (!page) return
    setMsg('保存中…')
    try {
      await api(`/aapi/v1/pages/${page.page_id}/review`, {
        method: 'PATCH',
        body: JSON.stringify({ expected_revision: page.page_revision, reviewer: 'web_review_ui', corrections: edits }),
      })
      setMsg('已保存修订。刷新页面读取新版本。')
      const m = await api<BookManifest>(`/v1/books/${bookId}/manifest`)
      setPages(m.pages as unknown as MPage[])
      setEdits({})
    } catch (e) {
      setMsg(`保存失败：${e instanceof Error ? e.message : '未知错误'}（版本冲突时请刷新后重试）`)
    }
  }

  return (
    <>
      <h1 className="page-title">原书校对{title && ` · ${title}`}</h1>
      <p className="page-sub">修正识别文字后保存；每次保存版本号递增，生成链路只消费已校对版本。</p>
      {msg && <div className="qa-status" style={{ marginBottom: 10 }}>{msg}</div>}
      <div className="review-grid">
        <div className="review-pages">
          {pages.map((p, i) => (
            <button key={p.page_id}
              className={`btn ghost ${p.page_id === sel ? 'sel' : ''}`}
              onClick={() => { setSel(p.page_id); setEdits({}); setMsg('') }}>
              第{i + 1}页{p.review_status === 'reviewed' ? ' ✓' : ''}
            </button>
          ))}
        </div>
        {page ? (
          <div className="review-detail">
            <div className="review-media">
              {page.image_asset_id && <img src={`/media/${page.image_asset_id}`} alt={`第${page.printed_page ?? ''}页`} />}
            </div>
            <div className="review-lines">
              <div className="book-meta">
                页版本 r{page.page_revision} · {page.ocr_lines?.length ?? 0} 行 · 低置信 {lowConf} 行（红色）
              </div>
              {(page.ocr_lines ?? []).map((l) => {
                const val = edits[l.line_id] ?? l.raw_text
                const changed = edits[l.line_id] !== undefined && edits[l.line_id] !== l.raw_text
                return (
                  <div key={l.line_id} className="ocr-line">
                    <span className={`conf ${l.confidence < 0.6 ? 'low' : ''}`}>{Math.round(l.confidence * 100)}%</span>
                    <input
                      value={val}
                      onChange={(e) => setEdits({ ...edits, [l.line_id]: e.target.value })}
                      style={{ borderColor: changed ? 'var(--accent)' : undefined }}
                    />
                  </div>
                )
              })}
              <button className="btn" onClick={save} style={{ marginTop: 12 }}>保存修订</button>
            </div>
          </div>
        ) : <div className="placeholder">选择左侧页面开始校对。</div>}
      </div>
    </>
  )
}
