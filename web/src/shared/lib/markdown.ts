import { esc } from './format'

/**
 * 极简 Markdown → HTML（报告由本项目的规则引擎生成，只用到这些语法）。
 * 先用 esc 转义，再还原粗体/行内代码/尖括号链接，避免报告内容注入 HTML。
 */
export function renderMd(src: string): string {
  const lines = String(src || '').split('\n')
  const out: string[] = []
  const inline = (s: string) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')

  let i = 0
  while (i < lines.length) {
    const ln = lines[i]
    if (/^\s*$/.test(ln)) {
      i++
      continue
    }
    // 表格：| a | b | 紧跟 |---|
    if (/^\|/.test(ln) && /^\|[\s\-:|]+\|?\s*$/.test(lines[i + 1] || '')) {
      const head = ln.split('|').slice(1, -1).map((s) => s.trim())
      i += 2
      const rows: string[][] = []
      while (i < lines.length && /^\|/.test(lines[i])) {
        rows.push(lines[i].split('|').slice(1, -1).map((s) => s.trim()))
        i++
      }
      out.push(
        '<table><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') +
        '</tbody></table>',
      )
      continue
    }
    if (/^### /.test(ln)) {
      out.push(`<h3>${inline(ln.slice(4))}</h3>`)
      i++
      continue
    }
    if (/^## /.test(ln)) {
      out.push(`<h2>${inline(ln.slice(3))}</h2>`)
      i++
      continue
    }
    if (/^# /.test(ln)) {
      out.push(`<h1>${inline(ln.slice(2))}</h1>`)
      i++
      continue
    }
    if (/^>\s?/.test(ln)) {
      const buf: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(inline(lines[i].replace(/^>\s?/, '')))
        i++
      }
      out.push(`<blockquote>${buf.join('<br>')}</blockquote>`)
      continue
    }
    if (/^---\s*$/.test(ln)) {
      out.push('<hr>')
      i++
      continue
    }
    if (/^\s*[-*] /.test(ln)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) {
        let item = lines[i].replace(/^\s*[-*] /, '')
        i++
        const sub: string[] = []
        while (i < lines.length && /^\s{2,}[-*] /.test(lines[i])) {
          sub.push(lines[i].replace(/^\s+[-*] /, ''))
          i++
        }
        items.push(`<li>${inline(item)}${sub.length ? '<ul>' + sub.map((s) => `<li>${inline(s)}</li>`).join('') + '</ul>' : ''}</li>`)
      }
      out.push(`<ul>${items.join('')}</ul>`)
      continue
    }
    if (/^\d+\. /.test(ln)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\d+\. /, ''))}</li>`)
        i++
      }
      out.push(`<ol>${items.join('')}</ol>`)
      continue
    }
    out.push(`<p>${inline(ln)}</p>`)
    i++
  }
  return out.join('')
}