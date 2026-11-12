export interface Envelope<T = unknown> {
  request_id: string
  status: 'ok' | 'accepted' | 'error'
  data: T | null
  error: { code: string; message: string; retryable: boolean } | null
  warnings: string[]
}

export class ApiError extends Error {
  code: string
  retryable: boolean
  http: number

  constructor(code: string, message: string, retryable: boolean, http: number) {
    super(message)
    this.code = code
    this.retryable = retryable
    this.http = http
  }
}

/** 统一交互服务请求。路径一律相对（同源或Vite代理）。 */
export async function api<T>(path: string, init?: RequestInit, timeoutMs = 8000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const resp = await fetch(path, {
      ...init,
      signal: init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
    const payload: Envelope<T> = await resp.json()
    if (payload.status === 'error' || payload.error || resp.status >= 400) {
      const e = payload.error
      throw new ApiError(e?.code ?? 'HTTP_ERROR', e?.message ?? `请求失败(${resp.status})`, e?.retryable ?? false, resp.status)
    }
    return payload.data as T
  } finally {
    clearTimeout(timer)
  }
}
