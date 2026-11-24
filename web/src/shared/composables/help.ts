import { reactive } from 'vue'

/** 帮助面板由页头统一挂载，任何页面都可以直接打开。 */
export const helpState = reactive({ open: false })

export function openHelp(): void {
  helpState.open = true
}

export function closeHelp(): void {
  helpState.open = false
}