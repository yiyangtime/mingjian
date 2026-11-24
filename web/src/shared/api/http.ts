/** 统一 fetch 封装：非 2xx 或 {error} 一律抛错，调用侧只处理一条错误路径。 */

export class ApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function parse<T>(r: Response): Promise<T> {
  let data: unknown = null
  try {
    data = await r.json()
  } catch {
    /* 非 JSON 响应（如 502 页面）下面统一报错 */
  }
  const err = (data as { error?: string } | null)?.error
  if (!r.ok || err) throw new ApiError(err || `请求失败（HTTP ${r.status}）`, r.status)
  return data as T
}

function qs(params?: Record<string, string | number | boolean | undefined>): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  get<T>(url: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
    return fetch(url + qs(params)).then((r) => parse<T>(r))
  },
  post<T>(url: string, body?: unknown): Promise<T> {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }).then((r) => parse<T>(r))
  },
  async text(url: string): Promise<string> {
    const r = await fetch(url)
    if (!r.ok) throw new ApiError(`请求失败（HTTP ${r.status}）`, r.status)
    return r.text()
  },

  /** 下载附件：失败时把后端 {error} 抛出来，避免点了按钮没反应。 */
  async download(url: string, fallback = 'download.bin'): Promise<string> {
    const r = await fetch(url)
    if (!r.ok) {
      let msg = `下载失败（HTTP ${r.status}）`
      try {
        const d = (await r.json()) as { error?: string } | null
        if (d?.error) msg = d.error
      } catch {
        /* 非 JSON 响应保留 HTTP 文案 */
      }
      throw new ApiError(msg, r.status)
    }
    let name = fallback
    const cd = r.headers.get('Content-Disposition') || ''
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd)
    if (m && m[1]) {
      try {
        name = decodeURIComponent(m[1].replace(/"/g, ''))
      } catch {
        name = m[1]
      }
    }
    const blob = await r.blob()
    const obj = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = obj
    a.download = name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(obj), 1500)
    return name
  },
}