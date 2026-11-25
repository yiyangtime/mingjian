/** 监测对象的展示与代码归类规则：视图与列表组件共用，避免两处各写一套判断。 */
import type { Monitor } from './types'

/** 服务端未返回上限时的兜底；真实上限以 /api/governance/system 的 limits.max_companies 为准 */
export const FALLBACK_MAX_MONITORS = 6

/** 北交所代码前缀：43/83/87/88/92（新三板转板与公开发行号段） */
const BSE_PREFIXES = ['43', '83', '87', '88', '92']

export function typeFromCode(code: string): string {
  const c = code.trim()
  if (!c) return '非上市企业'
  if (c.length === 5) return '上市公司 · 港股'
  if (BSE_PREFIXES.some((p) => c.startsWith(p))) return '上市公司 · 北交所'
  return '上市公司 · A股'
}

export function monitorType(m: Monitor): string {
  return m.type_label || typeFromCode(m.code || '')
}

/** 官网归一化：补协议并校验主机名；javascript: 之类的伪协议直接丢弃 */
export function siteUrl(raw?: string): string {
  const s = (raw || '').trim()
  if (!s) return ''
  try {
    const u = new URL(/^https?:\/\//i.test(s) ? s : `https://${s}`)
    return u.hostname.includes('.') ? u.href : ''
  } catch {
    return ''
  }
}

/** 列表里展示的官网名：去协议与 www，链接本身仍用 siteUrl 的完整地址 */
export function siteLabel(raw?: string): string {
  const u = siteUrl(raw)
  if (!u) return ''
  try {
    return new URL(u).hostname.replace(/^www\./i, '')
  } catch {
    return ''
  }
}