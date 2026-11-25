/**
 * 运行轮询状态机：/api/company/run/{id}?since= 的公共部分（企业洞察与竞品追踪共用）。
 *
 * 只负责「定时拉取 + 增量事件游标 + 卸载自动停表」，步骤与日志怎么渲染交给调用方钩子；
 * 组件离开时通过 onScopeDispose 停表，避免路由切走后仍有请求在跑。
 */
import { onScopeDispose, ref } from 'vue'
import { api } from '../api'
import type { RunEvent, RunPoll } from '../types'

export interface RunPollHooks {
  /** 每条增量事件（日志 / 步骤）回调一次 */
  onEvent?: (e: RunEvent) => void
  /** 每次轮询返回后回调，用于同步步骤条等整包字段 */
  onTick?: (d: RunPoll) => void
  /** 运行完成：正常结束或被取消后都有 result 为空的情况，由调用方决定退路 */
  onDone: (d: RunPoll) => void
  onFailed?: (d: RunPoll) => void
  onCancelled?: (d: RunPoll) => void
  /** 网络/服务错误：轮询已停止，调用方负责提示 */
  onError?: (err: Error) => void
}

export function useRunPolling(intervalMs = 800) {
  const runId = ref('')
  const active = ref(false)
  let timer: number | undefined
  let seq = 0

  function stop(): void {
    if (timer) {
      window.clearInterval(timer)
      timer = undefined
    }
    active.value = false
  }

  /** since 用于深链接重进：先回放已有事件，再从该游标继续增量拉取。 */
  function start(id: string, hooks: RunPollHooks, since = 0): void {
    stop()
    runId.value = id
    active.value = true
    seq = Math.max(0, Number(since) || 0)
    timer = window.setInterval(async () => {
      let d: RunPoll
      try {
        d = await api.get<RunPoll>(`/api/company/run/${id}`, { since: seq })
      } catch (e) {
        stop()
        hooks.onError?.(e as Error)
        return
      }
      if (d.events && d.events.length) {
        d.events.forEach((e) => hooks.onEvent?.(e))
        seq = d.next || seq
      }
      hooks.onTick?.(d)
      if (d.status === 'done') {
        stop()
        hooks.onDone(d)
      } else if (d.status === 'failed' || d.status === 'cancelled') {
        stop()
        if (d.status === 'failed') hooks.onFailed?.(d)
        else hooks.onCancelled?.(d)
      }
    }, intervalMs)
  }

  onScopeDispose(stop)

  return { runId, active, start, stop }
}