import { useEffect, useRef, useState } from 'react'

/** 视频舞台：真实视频播放 + 黑边/缩放校正后的归一化坐标点击 + 对象高亮。
 * 屏幕坐标 ≠ 视频内容坐标：必须换算到视频实际可见矩形内再做0-1归一化（B端手册B03/B06）。
 */
export interface NormalizedPoint {
  x: number
  y: number
}

export interface HighlightBox {
  bbox: [number, number, number, number] // [x1,y1,x2,y2] 归一化
  label?: string
}

interface VideoStageProps {
  src: string | null
  poster?: string
  subtitlesVtt?: string | null
  onTimeUpdate?: (tMs: number) => void
  onEnded?: () => void
  onPick?: (p: NormalizedPoint, tMs: number) => void
  onVideoRef?: (el: HTMLVideoElement | null) => void
  highlight?: HighlightBox | null
}

export default function VideoStage({
  src, poster, subtitlesVtt, onTimeUpdate, onEnded, onPick, onVideoRef, highlight,
}: VideoStageProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null)

  useEffect(() => {
    onVideoRef?.(videoRef.current)
    return () => onVideoRef?.(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  /** 视频实际可见矩形（扣除object-fit:contain产生的黑边）。 */
  function contentRect(): { left: number; top: number; width: number; height: number; pageLeft: number; pageTop: number } | null {
    const v = videoRef.current
    const wrap = wrapRef.current
    if (!v || !wrap || !v.videoWidth || !v.videoHeight) return null
    const r = wrap.getBoundingClientRect()
    const k = Math.min(r.width / v.videoWidth, r.height / v.videoHeight)
    const w = v.videoWidth * k
    const h = v.videoHeight * k
    return { left: (r.width - w) / 2, top: (r.height - h) / 2, width: w, height: h, pageLeft: r.left, pageTop: r.top }
  }

  function handleClick(e: React.MouseEvent) {
    if (!onPick) return
    const rect = contentRect()
    if (!rect) return
    const x = (e.clientX - rect.pageLeft - rect.left) / rect.width
    const y = (e.clientY - rect.pageTop - rect.top) / rect.height
    if (x < 0 || x > 1 || y < 0 || y > 1) return
    const tMs = Math.round((videoRef.current?.currentTime ?? 0) * 1000)
    onPick({ x, y }, tMs)
  }

  /** 高亮框屏幕定位（归一化→内容矩形→页面坐标）。 */
  function boxStyle(box: HighlightBox): React.CSSProperties | null {
    const rect = contentRect()
    if (!rect) return null
    const [x1, y1, x2, y2] = box.bbox
    return {
      left: rect.left + x1 * rect.width,
      top: rect.top + y1 * rect.height,
      width: (x2 - x1) * rect.width,
      height: (y2 - y1) * rect.height,
    }
  }

  return (
    <div ref={wrapRef} className="video-stage" onClick={handleClick} style={{ position: 'relative', cursor: onPick ? 'crosshair' : 'default' }}>
      {src && (
        <video
          ref={videoRef}
          src={src}
          poster={poster}
          playsInline
          onLoadedMetadata={() => {
            const v = videoRef.current
            if (v) setDims({ w: v.videoWidth, h: v.videoHeight })
          }}
          onTimeUpdate={() => onTimeUpdate?.(Math.round((videoRef.current?.currentTime ?? 0) * 1000))}
          onEnded={() => onEnded?.()}
          style={{ width: '100%', height: '100%', objectFit: 'contain', background: '#000' }}
        >
          {subtitlesVtt && <track kind="subtitles" src={subtitlesVtt} srcLang="zh" label="旁白" default />}
        </video>
      )}
      {highlight && (() => {
        const style = boxStyle(highlight)
        return style ? (
          <div className="pick-highlight" style={style}>
            {highlight.label && <span className="pick-label">{highlight.label}</span>}
          </div>
        ) : null
      })()}
      {dims && (
        <div className="video-dims">{dims.w}×{dims.h}</div>
      )}
    </div>
  )
}
