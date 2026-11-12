import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import ShelfPage from './pages/ShelfPage'
import PlayerPage from './pages/PlayerPage'
import IngestPage from './pages/IngestPage'
import ReviewPage from './pages/ReviewPage'
import ProgressPage from './pages/ProgressPage'

export default function App() {
  const [healthy, setHealthy] = useState<boolean | null>(null)

  useEffect(() => {
    fetch('/v1/health')
      .then((r) => r.json())
      .then((j) => setHealthy(Boolean(j?.data?.a_side)))
      .catch(() => setHealthy(false))
  }, [])

  return (
    <>
      <header className="app-header">
        <div className="brand">
          页语<small>原作可信互动伴读</small>
        </div>
        <nav className="nav">
          <NavLink to="/" end>书架</NavLink>
          <NavLink to="/import">导入绘本</NavLink>
        </nav>
        <span
          className={`health-dot ${healthy === false ? 'off' : ''}`}
          title={healthy === null ? '检查服务中' : healthy ? '服务正常 · A端已连接' : 'A端服务未连接'}
        />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<ShelfPage />} />
          <Route path="/read/:bookId" element={<PlayerPage />} />
          <Route path="/import" element={<IngestPage />} />
          <Route path="/review/:bookId" element={<ReviewPage />} />
          <Route path="/progress/:bookId" element={<ProgressPage />} />
        </Routes>
      </main>
    </>
  )
}
