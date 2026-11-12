/** 生成进度页：跟踪导入/理解/视频/配音任务的真实状态（GET /aapi/v1/runs/{id}）。 */
import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ClipRow } from '../api/types'

interface RunInfo {
  run_id: string
  step: string
  status: string
  error?: { code: string; message: string } | null
  usage?: Record<string, unknown>
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中', running: '运行中', succeeded: '成功', failed: '失败',
  cancelled: '已取消', needs_review: '待人工审核', expired: '已过期',
}

export default function ProgressPage() {
  const { bookId } = useParams()
  const [params] = useSearchParams()
  const [runId, setRunId] = useState(params.get('run') ?? '')
  const [run, setRun] = useState<RunInfo | null>(null)
  const [clips, setClips] = useState<ClipRow[]>([])
  const [msg, setMsg] = useState('')

  useEffect(() => {
    if (!runId || !bookId) return
    let alive = true
    const tick = async () => {
      try {
        const r = await api<RunInfo>(`/aapi/v1/runs/${runId}`)
        if (alive) setRun(r)
      } catch { /* 轮询失败静默重试 */ }
      try {
        const c = await api<{ clips: ClipRow[] }>(`/v1/books/${bookId}/clips?include_pending=true`)
        if (alive) setClips(c.clips.filter((x) => x.status !== 'expired'))
      } catch { /* ignore */ }
    }
    tick()
    const timer = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(timer) }
  }, [runId, bookId])

  return (
    <>
      <h1 className="page-title">生成进度</h1>
      <p className="page-sub">书籍 {bookId} 的生成任务实时状态（每4秒刷新）。</p>
      <div className="qa-form" style={{ marginBottom: 18 }}>
        <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="输入 run_id 跟踪任务" />
        <button className="btn" onClick={() => setMsg('')}>跟踪</button>
      </div>
      {msg && <div className="qa-status">{msg}</div>}
      {run && (
        <div className="run-card">
          <div className={`badge ${run.status === 'succeeded' ? '' : run.status === 'failed' ? 'mock' : ''}`}>
            {STATUS_LABEL[run.status] ?? run.status}
          </div>
          <div className="book-meta">步骤：{run.step}</div>
          {run.error && <div className="book-meta">错误：{run.error.code} {run.error.message}</div>}
        </div>
      )}
      <h3 style={{ margin: '20px 0 10px' }}>本视频片段状态</h3>
      {clips.length === 0 && <div className="placeholder">本书暂无生成片段。</div>}
      {clips.map((c) => (
        <div key={c.clip_id} className="run-card" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span className={`badge ${c.status === 'approved' ? '' : 'mock'}`}>{STATUS_LABEL[c.status] ?? c.status}</span>
          <span className="book-meta">{c.page_id} · v{c.asset_version} · {Math.round(c.duration_ms / 100) / 10}s</span>
          {c.motion_status && c.motion_status !== 'not_assessed' &&
            <span className="book-meta">动作:{c.motion_status}</span>}
        </div>
      ))}
    </>
  )
}
