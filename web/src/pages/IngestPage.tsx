/** 书籍导入页：上传获准绘本 → A端建立书籍与导入任务 → 跳转生成进度。 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export default function IngestPage() {
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const navigate = useNavigate()

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim() || !file) { setMsg('请填写书名并选择文件。'); return }
    setBusy(true); setMsg('正在上传并建立书籍…')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const resp = await fetch(`/v1/upload?title=${encodeURIComponent(title.trim())}`, { method: 'POST', body: fd })
      const j = await resp.json()
      if (j.error) { setMsg(`导入失败：${j.error.message}`); setBusy(false); return }
      setMsg(`已创建书籍，导入任务 ${j.data.run_id.slice(0, 18)}… 跳转进度页`)
      setTimeout(() => navigate(`/progress/${j.data.book_id}?run=${j.data.run_id}`), 900)
    } catch {
      setMsg('上传失败，请重试。'); setBusy(false)
    }
  }

  return (
    <>
      <h1 className="page-title">导入绘本</h1>
      <p className="page-sub">仅限组委会授权或团队拥有使用权的绘本；上传后自动进入识别与生成流程。</p>
      <form onSubmit={submit} className="ingest-form">
        <label>书名<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：长袜子皮皮来了" /></label>
        <label>绘本文件（PDF/EPUB/PNG/JPG，≤60MB）
          <input type="file" accept=".pdf,.epub,.png,.jpg,.jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <button className="btn" disabled={busy}>{busy ? '处理中…' : '开始导入'}</button>
      </form>
      {msg && <div className="qa-answer" style={{ marginTop: 16 }}>{msg}</div>}
      <div className="placeholder" style={{ marginTop: 20 }}>
        流程：上传 → 预处理（旋转/裁切/质量检测）→ OCR（Apple Vision）→ VLM理解 →
        人工校对 → 分镜 → 本机AI视频生成 → 配音 → 逐帧审核 → 发布播放。
      </div>
    </>
  )
}
