/**
 * 操作人署名：审计日志里记的是谁做的确认/修正。
 * 只存本机浏览器，不上传；默认「业务用户」，可在接入中心修改。
 */

const KEY = 'mj_actor'
export const DEFAULT_ACTOR = '业务用户'

export function actorName(): string {
  try {
    return (localStorage.getItem(KEY) || '').trim() || DEFAULT_ACTOR
  } catch {
    return DEFAULT_ACTOR
  }
}

export function setActorName(name: string): void {
  const v = name.trim().slice(0, 40)
  try {
    if (v && v !== DEFAULT_ACTOR) localStorage.setItem(KEY, v)
    else localStorage.removeItem(KEY)
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略即可 */
  }
}