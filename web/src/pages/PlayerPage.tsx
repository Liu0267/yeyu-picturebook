import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { BookManifest, ClipRow } from '../api/types'
import VideoStage, { type HighlightBox, type NormalizedPoint } from '../components/VideoStage'
import HoldToTalk from '../components/HoldToTalk'
import { nextPhase, type Phase, type PhaseEvent } from '../state/readingMachine'

interface ClipsPayload {
  clips: ClipRow[]
  frame_evidence: { f_id: string; t_ms: number; image_asset_id: string; claims: { claim: string; region?: { x1: number; y1: number; x2: number; y2: number } }[] }[]
}

interface Evidence {
  id: string
  source: 'book' | 'frame'
  page_id?: string
  printed_page?: string
  clip_id?: string
  version?: number
  ms?: number
}

interface TurnResult {
  kind?: 'command' | 'answer'
  command?: string
  answer_id?: string
  answer_text?: string
  status?: string
  evidence?: Evidence[]
  method?: string
}

/** 播放与交互主页：真实会话问答（/v1/turns）+ 触控识别（/v1/touches）+ 证据回放。 */
export default function PlayerPage() {
  const { bookId } = useParams()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [clips, setClips] = useState<ClipRow[]>([])
  const [pagesMap, setPagesMap] = useState<Record<string, { image?: string; printed?: string }>>({})
  const [idx, setIdx] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [tMs, setTMs] = useState(0)
  const [highlight, setHighlight] = useState<HighlightBox | null>(null)
  const [answer, setAnswer] = useState<{ text: string; status: string; evidence: Evidence[] } | null>(null)
  const [question, setQuestion] = useState('')
  const [status, setStatus] = useState('')
  const [pageImage, setPageImage] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const seqRef = useRef(0)
  const sessionRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // D7：显式阅读状态机（替代布尔拼接）+ 打断续读
  const [phase, setPhase] = useState<Phase>('READING')
  const phaseRef = useRef<Phase>('READING')
  const resumeAtRef = useRef<number | null>(null) // 提问时刻的播放位置（ms），回答后恢复

  function transition(event: PhaseEvent) {
    const next = nextPhase(phaseRef.current, event)
    phaseRef.current = next
    setPhase(next)
    return next
  }

  /** 暂停并记住播放位置（打断续读的关键：问答后回到此位置）。 */
  function pauseForInteraction() {
    const v = videoRef.current
    if (v && !v.paused) {
      resumeAtRef.current = Math.round(v.currentTime * 1000)
      v.pause()
    } else if (v) {
      resumeAtRef.current = Math.round(v.currentTime * 1000)
    }
  }

  /** 回答结束后恢复阅读：回到提问时刻位置继续播放（翻页/切书后禁止恢复由load()清理resumeAtRef保证）。 */
  function resumeReading() {
    const at = resumeAtRef.current
    resumeAtRef.current = null
    transition('RESUMED')
    const v = videoRef.current
    if (v && at !== null && clip && Math.abs(at - tMs) < clip.duration_ms) {
      v.currentTime = at / 1000
      v.play().catch(() => setStatus('请按播放继续阅读。'))
    }
  }

  useEffect(() => {
    if (!bookId) return
    api<BookManifest>(`/v1/books/${bookId}/manifest`).then((m) => {
      setTitle(m.title)
      const map: Record<string, { image?: string; printed?: string }> = {}
      for (const p of m.pages ?? []) {
        map[p.page_id] = { image: p.image_asset_id ? `/media/${p.image_asset_id}` : undefined, printed: p.printed_page }
      }
      setPagesMap(map)
    }).catch((e) => setError(e.message))
    api<ClipsPayload>(`/v1/books/${bookId}/clips`)
      .then((c) => {
        setClips(c.clips)
        if (!c.clips.length) setStatus('本书暂无已审核的互动视频片段。')
      })
      .catch((e) => setError(e.message))
    // 创建阅读会话
    api<{ id: string }>('/v1/sessions', { method: 'POST', body: '{}' })
      .then((s) => { sessionRef.current = s.id })
      .catch(() => setStatus('会话创建失败，问答不可用。'))
  }, [bookId])

  const clip = clips[idx]

  /** 提问时刻冻结的阅读快照（问答/点选请求必带，版本不匹配服务端返回409）。 */
  const snapshot = useCallback(() => ({
    book_id: bookId,
    clip_id: clip?.clip_id ?? null,
    asset_version: clip?.asset_version ?? null,
    media_time_ms: Math.min(clip?.duration_ms ?? 0, tMs),
  }), [bookId, clip, tMs])

  function load(i: number, atMs = 0) {
    if (!clips[i]) return
    seqRef.current++
    abortRef.current?.abort()
    resumeAtRef.current = null // 翻页后禁止恢复旧书位置（B端手册T08）
    phaseRef.current = 'READING'
    setPhase('READING')
    setIdx(i)
    setTMs(atMs)
    setHighlight(null)
    setAnswer(null)
    setPlaying(false)
    requestAnimationFrame(() => {
      if (videoRef.current && atMs) videoRef.current.currentTime = atMs / 1000
    })
  }

  /** 服务端意图命令 → 本地媒体动作。 */
  function runCommand(cmd?: string) {
    const v = videoRef.current
    if (!v) return
    if (cmd === 'pause') v.pause()
    else if (cmd === 'resume' || cmd === 'replay') {
      if (cmd === 'replay') v.currentTime = 0
      v.play().catch(() => setStatus('请按播放继续阅读。'))
    } else if (cmd === 'page_prev') load(idx - 1)
    else if (cmd === 'page_next') load(idx + 1)
    else if (cmd === 'cancel') { v.pause(); setStatus('已停止。按播放继续阅读。') }
  }

  function nextSeq() { seqRef.current += 1; return seqRef.current }

  /** 浏览器TTS播报（P0兜底）；播报开始→SPEAKING，结束→RESUMING并恢复阅读。 */
  function speak(text: string) {
    transition('ANSWER_READY')
    try {
      const synth = window.speechSynthesis
      if (!synth || !text) { // 无朗读能力：直接恢复
        transition('ANSWER_DONE')
        setTimeout(resumeReading, 1500)
        return
      }
      synth.cancel()
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'zh-CN'
      u.rate = 1.0
      u.onend = () => { transition('ANSWER_DONE'); setTimeout(resumeReading, 800) }
      u.onerror = () => { transition('ANSWER_DONE'); setTimeout(resumeReading, 800) }
      synth.speak(u)
    } catch {
      transition('ANSWER_DONE')
      setTimeout(resumeReading, 1500)
    }
  }

  /** 语音结果与文字问答共用处理。 */
  function handleTurnResult(a: TurnResult, questionEcho?: string) {
    if (a.kind === 'command') { runCommand(a.command); setQuestion(''); setStatus(''); speak('好的'); return }
    setAnswer({ text: a.answer_text ?? '', status: a.status ?? '', evidence: a.evidence ?? [] })
    setStatus(a.status === 'uncertain' ? '证据不足，已如实告知。' : '')
    setQuestion('')
    if (a.answer_text) speak(a.answer_text)
    void questionEcho
  }

  function togglePlay() {
    const v = videoRef.current
    if (!v) return
    if (v.paused) v.play().catch(() => setStatus('无法播放，请刷新重试。'))
    else v.pause()
  }

  async function ask(e?: React.FormEvent) {
    e?.preventDefault()
    const sid = sessionRef.current
    if (!clip || !question.trim()) return
    if (!sid) { setStatus('会话未就绪，请刷新页面。'); return }
    const n = nextSeq()
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    pauseForInteraction()
    transition('ASK')
    setAnswer(null)
    setStatus('正在查找原书和画面证据…')
    try {
      const a = await api<TurnResult>('/v1/turns', {
        method: 'POST', signal: controller.signal,
        headers: { 'X-Reading-Session': sid },
        body: JSON.stringify({
          session_id: sid, seq: n, turn_id: `turn-${n}`,
          input: { kind: 'text', text: question.trim() },
          snapshot: snapshot(),
        }),
      })
      if (n !== seqRef.current) return
      if (a.kind === 'command') { runCommand(a.command); setQuestion(''); setStatus(''); transition('RESUMED'); return }
      setAnswer({ text: a.answer_text ?? '', status: a.status ?? '', evidence: a.evidence ?? [] })
      setStatus(a.status === 'uncertain' ? '证据不足，已如实告知。' : '')
      setQuestion('')
      if (a.status === 'uncertain') { // 不确定回答不朗读，短暂提示后恢复
        transition('ANSWER_READY'); transition('ANSWER_DONE')
        setTimeout(resumeReading, 1200)
        return
      }
      speak(a.answer_text ?? '')
    } catch (err) {
      if (n === seqRef.current && !(err instanceof DOMException && err.name === 'AbortError')) {
        transition('ERROR')
        setAnswer(null)
        setStatus(err instanceof ApiError ? `${err.message} 可重试。` : '网络异常，请重试。')
        setTimeout(() => transition('RETRY'), 1500)
      }
    }
  }

  async function onPick(p: NormalizedPoint, _pickMs: number) {
    const sid = sessionRef.current
    if (!clip || !sid) return
    const n = nextSeq()
    abortRef.current?.abort()
    pauseForInteraction()
    transition('ASK')
    setStatus('正在识别画面…')
    setAnswer(null)
    try {
      const r = await api<{ object: { name?: string; description?: string; bbox?: number[] } | null; evidence: Evidence | null; hint?: string }>(
        '/v1/touches',
        {
          method: 'POST',
          headers: { 'X-Reading-Session': sid },
          body: JSON.stringify({ session_id: sid, seq: n, snapshot: snapshot(), x: p.x, y: p.y }),
        },
      )
      if (n !== seqRef.current) return
      if (!r.object) {
        setHighlight(null)
        setStatus(r.hint ?? '没有可确认的对象。')
        setAnswer(null)
        transition('ANSWER_READY'); transition('ANSWER_DONE')
        setTimeout(resumeReading, 1000)
        return
      }
      if (r.object.bbox) setHighlight({ bbox: r.object.bbox as [number, number, number, number], label: r.object.name ?? undefined })
      const text = r.object.name ? `这是${r.object.name}。${r.object.description ? '画面里，' + r.object.description : ''}` : (r.object.description ?? '')
      setAnswer({
        text,
        status: 'answered',
        evidence: r.evidence ? [r.evidence] : [],
      })
      setStatus('')
      speak(text)
    } catch (err) {
      if (n === seqRef.current) {
        setStatus(err instanceof ApiError ? `${err.message} 可重试。` : '识别失败，请重试。')
        transition('ERROR'); setTimeout(() => transition('RETRY'), 1200)
      }
    }
  }

  /** 证据回放：frame→跳到视频时间；book→显示原页图。 */
  function jumpEvidence(ev: Evidence) {
    if (ev.source === 'frame' && ev.clip_id) {
      const i = clips.findIndex((c) => c.clip_id === ev.clip_id && c.asset_version === ev.version)
      if (i < 0) { setStatus('证据对应的旧版本已不可用。'); return }
      load(i, ev.ms ?? 0)
      setPageImage(null)
    } else if (ev.source === 'book' && ev.page_id) {
      const info = pagesMap[ev.page_id]
      if (info?.image) setPageImage(info.image)
      else setStatus('该证据对应页面没有可显示的原图。')
    }
  }

  if (error) {
    return (
      <>
        <h1 className="page-title">加载失败</h1>
        <div className="placeholder">{error}（请确认A端与交互服务已启动）</div>
      </>
    )
  }

  return (
    <>
      <div className="player-head">
        <h1 className="page-title">{title || '加载中…'}</h1>
        <span className={`badge ${phase === 'READING' ? '' : 'mock'}`} style={{ alignSelf: 'center' }}>
          {phase === 'READING' ? '阅读中' : phase === 'PAUSED' ? '已暂停' : phase === 'LISTENING' ? '聆听中'
            : phase === 'THINKING' ? '思考中' : phase === 'SPEAKING' ? '播报中' : phase === 'RESUMING' ? '恢复阅读' : '重试'}
        </span>
        <button className="btn ghost" onClick={() => navigate(`/progress/${bookId}`)}>生成进度</button>
        <button className="btn ghost" onClick={() => navigate(`/review/${bookId}`)}>原书校对</button>
      </div>
      <p className="page-sub">
        {clip ? `第 ${idx + 1} / ${clips.length} 段 · ${Math.round(clip.duration_ms / 100) / 10}s · v${clip.asset_version}`
              : '等待可播放片段'}
        {clip?.is_mock && <span className="badge mock" style={{ marginLeft: 10 }}>演示数据</span>}
        <span style={{ marginLeft: 10, color: 'var(--ink-soft)', fontSize: 13 }}>本样书为开发调试样本 · 非组委会授权绘本</span>
      </p>

      <div className="player-grid">
        <div className="player-stage">
          {clip ? (
            <VideoStage
              src={`/media/${clip.asset_id}`}
              onVideoRef={(el) => {
                videoRef.current = el
              }}
              onTimeUpdate={(t) => {
                setTMs(t)
                const paused = videoRef.current?.paused
                if (paused !== undefined && playing === paused) setPlaying(!paused)
              }}
              onEnded={() => { if (clips[idx + 1]) load(idx + 1) }}
              onPick={onPick}
              highlight={highlight}
            />
          ) : (
            <div className="placeholder">暂无已审核片段</div>
          )}

          {clip && (
            <div className="player-controls">
              <button className="btn ghost" disabled={idx === 0} onClick={() => load(idx - 1)}>上一页</button>
              <button className="btn" onClick={togglePlay}>{playing ? '暂停' : '播放'}</button>
              <button className="btn ghost" onClick={() => load(idx)}>重播</button>
              <button className="btn ghost" disabled={idx === clips.length - 1} onClick={() => load(idx + 1)}>下一页</button>
              <input
                type="range" min={0} max={clip.duration_ms / 1000} step={0.1}
                value={tMs / 1000}
                onChange={(e) => { const v = videoRef.current; if (v) { v.currentTime = Number(e.target.value); setTMs(Number(e.target.value) * 1000) } }}
                style={{ flex: 1 }}
              />
              <span className="book-meta">{Math.round(tMs / 100) / 10}s</span>
            </div>
          )}
          <p className="book-meta" style={{ marginTop: 8 }}>
            提示：视频画面可直接点击角色或物品识别；语音输入在后续版本接入。
          </p>
        </div>

        <div className="qa-panel">
          <div className="qa-status">{status}</div>
          <form onSubmit={ask} className="qa-form">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="问点什么，比如「猫为什么要靴子？」"
            />
            <button className="btn" type="submit" disabled={!clip}>提问</button>
            <HoldToTalk
              disabled={!clip}
              sessionRef={sessionRef}
              snapshotProvider={snapshot}
              seqProvider={{ next: () => nextSeq(), current: () => seqRef.current }}
              abortRef={abortRef}
              onResult={(a) => {
                if (a.status === 'mic_error') { setStatus('无法开启麦克风，请检查权限或用文字输入。'); return }
                if (a.status === 'too_short') { setStatus('说话时间太短，请按住后再说。'); return }
                if (a.status === 'no_session') { setStatus('会话未就绪，请刷新页面。'); return }
                if (a.status === 'network_error') { setStatus('网络异常，请重试。'); return }
                if (a.status === 'error') { setStatus((a.answer_text ?? '识别失败') + ' 可重试。'); return }
                handleTurnResult(a as TurnResult)
              }}
              onPhase={(p) => {
                if (p === 'recording') { setStatus('正在听…'); setAnswer(null); pauseForInteraction(); transition('HOLD_START') }
                else if (p === 'uploading') { transition('HOLD_END'); setStatus('正在查找原书和画面证据…') }
              }}
            />
          </form>
          {answer && (
            <div className="qa-answer">
              <div>{answer.text}</div>
              {answer.status === 'uncertain' && <div className="qa-evidence-none">（本回答未引用依据）</div>}
              {answer.evidence.length > 0 && (
                <div className="qa-evidence">
                  {answer.evidence.map((ev) => (
                    <button key={ev.id} className="ev-btn" onClick={() => jumpEvidence(ev)}>
                      {ev.source === 'frame'
                        ? `回到画面 ${(ev.ms ?? 0) / 1000}s`
                        : `查看原书 ${ev.printed_page ?? ev.page_id ?? ''}`}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {pageImage && (
            <div className="page-pop" onClick={() => setPageImage(null)}>
              <img src={pageImage} alt="原书页" />
              <span className="page-pop-hint">点击关闭</span>
            </div>
          )}
          <div className="placeholder" style={{ marginTop: 14 }}>
            语音输入（按住说话）与朗读在 D6 接入；追问、打断与恢复在 D7 接入。
          </div>
        </div>
      </div>
    </>
  )
}
