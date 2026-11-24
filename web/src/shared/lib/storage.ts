/** localStorage 安全读写：隐私模式、配额满、旧数据损坏都不抛错，读失败回退默认值。 */
export function readRaw(key: string): string {
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

export function writeRaw(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* 隐私模式或配额满：界面状态降级为不持久化 */
  }
}

export function readJson<T>(key: string, fallback: T): T {
  const raw = readRaw(key)
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 隐私模式或配额满：界面状态降级为不持久化 */
  }
}