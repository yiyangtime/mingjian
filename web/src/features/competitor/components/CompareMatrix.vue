<script setup lang="ts">
/** 横向对比矩阵：纯展示，格子里的条数、推断与置信度原样呈现，不做二次加工。 */
import type { ComparePack, Judgment, MatrixRow } from '../../../shared/types'

defineProps<{
  packs: ComparePack[]
  rows: MatrixRow[]
  judgments: Judgment[]
  selfCompany: string
}>()
</script>

<template>
  <div v-if="!rows.length" class="empty">暂无可对比事实</div>
  <template v-else>
    <div class="mtx">
      <table>
        <thead>
          <tr>
            <th>维度</th>
            <th v-if="selfCompany" class="c self-col">
              {{ selfCompany }}<div class="self-sub">我方 · 企业自述</div>
            </th>
            <th v-for="p in packs" :key="p.company" class="c">{{ p.company }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.label">
            <td class="dim">{{ r.label }}</td>
            <td v-if="selfCompany" class="c self-cell">
              <span v-if="r.cells[selfCompany]?.n" class="pill-self">有自述</span>
              <span v-else class="dash">—</span>
            </td>
            <td v-for="p in packs" :key="p.company" class="c">
              <span
                v-if="r.cells[p.company]?.n" class="pill-n"
                :title="`本期 ${r.cells[p.company].n} 条事实`"
              >{{ r.cells[p.company].n }}</span>
              <span v-else class="dash">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-for="j in judgments" :key="j.id" class="judge">
      <span class="jt">推断 {{ j.id }}</span>{{ j.text }}
      <div class="jb">依据 {{ j.basis }}　|　置信度：{{ j.conf }}</div>
    </div>
  </template>
</template>

<style scoped>
.mtx { overflow: auto; }
.mtx table { width: 100%; border-collapse: collapse; }
.mtx th, .mtx td { padding: 11px 13px; border-bottom: 1px solid #F1F3F6; font-size: 13px; }
.mtx th { background: #F8FAFC; color: var(--mute); font-size: 12px; font-weight: 600; text-align: left; white-space: nowrap; }
.mtx th.c, .mtx td.c { text-align: center; }
.mtx .self-col { background: #FFFBF0; border-bottom-color: #F0E2BE; color: #8A6A1F; font-weight: 600; }
.self-sub { font-weight: 400; font-size: 10.5px; color: #B08948; margin-top: 2px; }
.mtx .dim { font-weight: 550; }
.mtx .self-cell { background: #FFFDF7; }
.pill-n {
  display: inline-block; min-width: 22px; padding: 1px 7px; border-radius: 20px;
  background: #EFF3FF; color: #1D4ED8; font-size: 11.5px;
}
.pill-self { display: inline-block; padding: 1px 7px; border-radius: 20px; background: #FFF3D6; color: #8A6A1F; font-size: 11.5px; }
.dash { color: #C4CAD2; }
.judge {
  background: #FFF9F0; border-left: 3px solid #E0A030; border-radius: 0 8px 8px 0;
  padding: 12px 15px; margin-top: 10px; line-height: 1.9; font-size: 13px;
}
.jt {
  font-size: 10.5px; color: #B45309; background: #FEF0DC; border-radius: 3px;
  padding: 1px 6px; margin-right: 7px;
}
.jb { font-size: 11.5px; color: #8A7B5F; margin-top: 4px; }
</style>