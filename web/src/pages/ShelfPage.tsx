import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'

interface BookRow {
  book_id: string
  title: string
  is_mock: boolean
  page_count: number
  revision: number
  rights_note?: string
}

export default function ShelfPage() {
  const [books, setBooks] = useState<BookRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    api<BookRow[]>('/v1/books')
      .then(setBooks)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <>
      <h1 className="page-title">我的书架</h1>
      <p className="page-sub">选择一本绘本开始互动阅读。已审核的真实AI成片会在这里出现。</p>
      {loading && <div className="placeholder">正在加载书目…</div>}
      {error && <div className="placeholder">加载失败：{error}（请确认交互服务与A端服务已启动）</div>}
      {!loading && !error && books.length === 0 && (
        <div className="placeholder">书架暂时是空的。可以先到「导入绘本」添加书籍，等待生成完成后回来。</div>
      )}
      <div className="book-grid">
        {books.map((b) => (
          <div key={b.book_id} className="book-card" onClick={() => navigate(`/read/${b.book_id}`)}>
            <span className={`badge ${b.is_mock ? 'mock' : ''}`}>{b.rights_note}</span>
            <h3>{b.title}</h3>
            <div className="book-meta">{b.page_count} 页 · 版本 r{b.revision}</div>
            <div className="book-meta">进入阅读 →</div>
          </div>
        ))}
      </div>
    </>
  )
}
