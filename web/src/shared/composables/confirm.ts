import { reactive } from 'vue'

export interface ConfirmOptions {
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

interface ConfirmState extends ConfirmOptions {
  open: boolean
  resolve?: (v: boolean) => void
}

/** 全站共用一个确认框：调用处 await 一行，不再用 window.confirm 打断视觉。 */
export const confirmState = reactive<ConfirmState>({
  open: false,
  title: '',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
})

export function confirmAsk(opts: ConfirmOptions): Promise<boolean> {
  // 连续弹两个时，前一个按取消处理，避免 promise 永远悬着
  confirmState.resolve?.(false)
  Object.assign(confirmState, {
    message: '',
    confirmText: '确定',
    cancelText: '取消',
    danger: false,
    ...opts,
    open: true,
  })
  return new Promise<boolean>((resolve) => {
    confirmState.resolve = resolve
  })
}

export function settleConfirm(v: boolean): void {
  confirmState.open = false
  const r = confirmState.resolve
  confirmState.resolve = undefined
  r?.(v)
}