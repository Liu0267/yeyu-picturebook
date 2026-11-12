import { useEffect, useRef, useState } from 'react'

/** 按住说话组件：MediaRecorder 录音（≤8s），松开自动上传。
 * 交互约定（B端手册B05）：按下即 LISTENING（隐含打断旧播报），松开即提交。
 */
interface HoldToTalkProps {
  disabled?: boolean
  sessionRef: React.MutableRefObject<string | null>
  snapshotProvider: () => Record<string, unknown>
  seqProvider: { next: () => number; current: () => number }
  abortRef: React.MutableRefObject<AbortController | null>
  onResult: (r: { answer_text?: string; status?: string; evidence?: unknown[]; asr_text?: string; kind?: string; command?: string }) => void
  onPhase: (p: 'idle' | 'recording' | 'uploading') => void
}

const MAX_MS = 8000

export default function HoldToTalk({
  disabled, sessionRef, snapshotProvider, seqProvider, abortRef, onResult, onPhase,
}: HoldToTalkProps) {
  const [phase, setPhase] = useState<'idle' | 'recording' | 'uploading'>('idle')
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<number | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  useEffect(() => () => {
    timerRef.current && clearTimeout(timerRef.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
  }, [])

  function setP(p: 'idle' | 'recording' | 'uploading') { setPhase(p); onPhase(p) }

  async function start() {
    if (disabled || phase !== 'idle') return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      rec.onstop = () => finish()
      rec.start()
      recorderRef.current = rec
      setP('recording')
      timerRef.current = window.setTimeout(() => stop(), MAX_MS)
    } catch {
      onResult({ status: 'mic_error' })
      setStatus()
    }
  }

  function setStatus() { /* placeholder: 上层通过onResult得知麦克风错误 */ }

  function stop() {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    recorderRef.current?.state === 'recording' && recorderRef.current.stop()
  }

  async function finish() {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    const blob = new Blob(chunksRef.current, { type: recorderRef.current?.mimeType || 'audio/webm' })
    recorderRef.current = null
    if (blob.size < 2000) { setP('idle'); onResult({ status: 'too_short' }); return }
    setP('uploading')
    const sid = sessionRef.current
    if (!sid) { setP('idle'); onResult({ status: 'no_session' }); return }
    const n = seqProvider.next()
    abortRef.current?.abort()
    const snap = snapshotProvider()
    const fd = new FormData()
    fd.append('audio', blob, 'speech.webm')
    try {
      const resp = await fetch(`/v1/audio?session_id=${encodeURIComponent(sid)}&seq=${n}` +
        `&turn_id=turn-${n}&book_id=${encodeURIComponent(String(snap.book_id ?? ''))}` +
        `&clip_id=${encodeURIComponent(String(snap.clip_id ?? ''))}` +
        `&asset_version=${snap.asset_version ?? ''}&media_time_ms=${snap.media_time_ms ?? 0}`,
        { method: 'POST', headers: { 'X-Reading-Session': sid }, body: fd, signal: abortRef.current?.signal })
      const j = await resp.json()
      if (n !== seqProvider.current()) return
      if (j.error) { onResult({ status: 'error', answer_text: j.error.message }); return }
      onResult(j.data)
    } catch (e) {
      if (n === seqProvider.current()) onResult({ status: 'network_error' })
    } finally {
      setP('idle')
    }
  }

  return (
    <button
      type="button"
      className={`btn holdtalk ${phase === 'recording' ? 'recording' : ''}`}
      disabled={disabled || phase === 'uploading'}
      onMouseDown={start}
      onMouseUp={stop}
      onTouchStart={(e) => { e.preventDefault(); start() }}
      onTouchEnd={(e) => { e.preventDefault(); stop() }}
    >
      {phase === 'recording' ? '松开 结束' : phase === 'uploading' ? '识别中…' : '按住 说话'}
    </button>
  )
}
