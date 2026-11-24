/** 小工具：HTML 转义（只用于 v-html 之前的拼接）、下载、时间戳、延时。 */

export function esc(s: unknown): string {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string
  ))
}

export function stamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function nowText(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

export function download(content: BlobPart, name: string, type: string): void {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** UTF-8 安全的 base64（替代已废弃的 unescape+btoa 组合）。 */
export function b64EncodeUtf8(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let bin = ''
  const step = 8192
  for (let i = 0; i < bytes.length; i += step) {
    bin += String.fromCharCode(...bytes.subarray(i, i + step))
  }
  return btoa(bin)
}

export function b64EncodeBytes(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let bin = ''
  const step = 8192
  for (let i = 0; i < bytes.length; i += step) {
    bin += String.fromCharCode(...bytes.subarray(i, i + step))
  }
  return btoa(bin)
}

/* ---------- 展示与交互 ---------- */

export function parseTime(s?: string): Date | null {
  if (!s) return null
  const d = new Date(s.includes('T') ? s : s.replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d
}

/** 后端时间是本地 `YYYY-MM-DD HH:MM:SS`，这里只做相对时间，不引日期库。 */
export function relTime(s?: string): string {
  const d = parseTime(s)
  if (!d) return s || '—'
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 0) return s as string
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return String(s).slice(0, 10)
}

export function durationText(sec?: number): string {
  const n = Math.max(0, Math.round(Number(sec) || 0))
  if (n < 60) return `${n} 秒`
  const m = Math.floor(n / 60)
  const r = n % 60
  return r ? `${m} 分 ${r} 秒` : `${m} 分钟`
}

export function fmtBytes(n?: number): string {
  const v = Number(n) || 0
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`
  return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function statusLabel(s: string): string {
  const m: Record<string, string> = {
    queued: '排队中', running: '运行中', done: '已完成', failed: '失败', cancelled: '已取消',
  }
  return m[s] || s || '—'
}

export function statusCls(s: string): string {
  const m: Record<string, string> = {
    queued: 'queue', running: 'run', done: 'ok', failed: 'err', cancelled: 'warn',
  }
  return m[s] || 'mute'
}

/** 复制到剪贴板；非安全上下文（局域网 http）退回 execCommand。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    /* 继续走兜底 */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}