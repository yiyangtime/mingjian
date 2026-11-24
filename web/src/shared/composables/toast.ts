import { reactive } from 'vue'

/** 全局单例 toast：任何组件调 toast('...') 即可，AppToast 负责渲染。 */
export const toastState = reactive({ msg: '', on: false })

let timer: number | undefined

export function toast(msg: string, ms = 2600): void {
  toastState.msg = msg
  toastState.on = true
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    toastState.on = false
  }, ms)
}