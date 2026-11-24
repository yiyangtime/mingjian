<script setup lang="ts">
import SlideDrawer from './SlideDrawer.vue'
import { closeHelp, helpState } from '../composables/help'

defineProps<{ version?: string }>()

const pages = [
  { to: '/', name: '公司情报', what: '这家公司最近发生了什么', how: '巨潮公告 + 东财资讯' },
  { to: '/policy', name: '政策公告', what: '所在产业有哪些政策与补贴', how: '工信部 / 省工信厅官网，也可上传文件' },
  { to: '/competitor', name: '竞品周报', what: '同一件事上谁在动、动得多快', how: '逐家跑完整链路后按维度对比' },
  { to: '/metrics', name: '指标联动', what: '当期最值得追的产业问题是什么', how: '定期报告指标 + 主营构成' },
  { to: '/agent', name: '智能体', what: '能执行哪些动作、记住了什么', how: '技能白名单 + 跨运行记忆' },
  { to: '/settings', name: '模型配置', what: '接哪家模型、系统运行得怎样', how: 'OpenAI 兼容服务商，保存即生效' },
]
</script>

<template>
  <SlideDrawer :open="helpState.open" title="使用说明" subtitle="30 秒上手 · 明鉴 MingJian" @close="closeHelp">
    <section class="hero">
      <p class="lead">输入一个公司名，程序自己联网采集、读原文、抽事实、做比对、写报告、留痕。<b>每条结论都能翻回原文。</b></p>
      <ol class="steps">
        <li><span>1</span><div><b>选主体</b><br>在公司情报页输入公司名或证券代码。</div></li>
        <li><span>2</span><div><b>看执行</b><br>七步实时日志，可取消；失败或取消后能从断点续跑。</div></li>
        <li><span>3</span><div><b>核事实、导报告</b><br>点事实看证据句与原文，确认/存疑都会写审计日志。</div></li>
      </ol>
    </section>

    <section>
      <h4>六个工作台</h4>
      <div class="pages">
        <RouterLink v-for="p in pages" :key="p.to" :to="p.to" class="page" @click="closeHelp">
          <div class="pn">{{ p.name }}</div>
          <div class="pw">{{ p.what }}</div>
          <div class="ph">{{ p.how }}</div>
        </RouterLink>
      </div>
    </section>

    <section>
      <h4>几条不会让步的规则</h4>
      <ul class="rules">
        <li><b>没有证据句，就不产事实。</b>每条事实绑定来源文档与链接；抽不到就退回标题并降置信度。</li>
        <li><b>缺失就留空。</b>找不到日期、金额就留空，不用推测值填充；缺口单独列出来。</li>
        <li><b>事实与判断分开。</b>判断逐条挂事实编号，可以精确反驳某一条，而不是推翻整篇。</li>
        <li><b>单份失败不阻断整批。</b>图片型 PDF、反爬、超时只影响那一份，其余继续。</li>
        <li><b>取不到 ≠ 没动作。</b>站点不可达、检索无结果会分别说明，不混为一谈。</li>
      </ul>
    </section>

    <section>
      <h4>深链接与快捷键</h4>
      <ul class="rules">
        <li><code>/?run=&lt;运行编号&gt;</code> 直接打开某次结果，可用于汇报或分享给同事。</li>
        <li><code>/?run=&lt;编号&gt;&amp;tab=report</code> 定位到报告；<code>&amp;fact=F01</code> 弹出某条事实。</li>
        <li><code>Esc</code> 关闭抽屉与弹窗；在主体输入框按 <code>Enter</code> 直接开始分析。</li>
      </ul>
    </section>

    <section>
      <h4>数据与隐私</h4>
      <ul class="rules">
        <li>采集、事实、报告、审计都存在本机 <code>data/mingjian.db</code> 与 <code>output/</code>，不上传第三方平台。</li>
        <li>联网只发生在采集公开信息与调用你配置的模型服务商时。</li>
        <li>确认、修正、删除会连同操作人署名与时间写入审计日志；署名只存本机浏览器，可在模型配置页修改。</li>
        <li>模型密钥只回显尾号；不配模型也能用规则引擎完整跑通。</li>
      </ul>
    </section>

    <div class="foot">
      明鉴 {{ version ? 'v' + version : '' }} · 本地服务 · 单进程运行（作业队列与调度线程在内存里）
    </div>
  </SlideDrawer>
</template>

<style scoped>
section { margin-bottom: 22px; }
h4 { margin: 0 0 10px; font-size: 13px; font-weight: 650; }
.hero { background: #F7F9FC; border: 1px solid #E6ECF5; border-radius: 10px; padding: 14px 16px; }
.lead { margin: 0 0 12px; font-size: 12.5px; color: var(--sub); line-height: 1.9; }
.lead b { color: var(--ink); }
.steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.steps li { display: flex; gap: 10px; font-size: 12px; color: var(--sub); line-height: 1.7; }
.steps li span {
  flex: 0 0 20px; width: 20px; height: 20px; border-radius: 50%;
  background: var(--accent); color: #fff; display: grid; place-items: center;
  font-size: 11px; font-weight: 600; margin-top: 1px;
}
.steps b { color: var(--ink); font-weight: 600; }

.pages { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.page {
  display: block; border: 1px solid var(--line); border-radius: 9px;
  padding: 9px 11px; text-decoration: none; color: inherit; transition: .14s;
}
.page:hover { border-color: var(--accent); background: var(--accent-soft); }
.pn { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.pw { font-size: 11.5px; color: var(--sub); margin-top: 2px; line-height: 1.6; }
.ph { font-size: 10.5px; color: var(--mute); margin-top: 2px; }

.rules { margin: 0; padding-left: 18px; font-size: 12px; color: var(--sub); line-height: 1.9; }
.rules li { margin: 4px 0; }
.rules b { color: var(--ink); font-weight: 600; }
.rules code {
  background: #F1F3F6; border-radius: 4px; padding: 1px 5px;
  font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: #374151;
}
.foot { padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11px; color: var(--mute); line-height: 1.8; }
</style>