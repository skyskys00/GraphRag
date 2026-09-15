const CITE_RE = /\[(\d+)\]/g

/**
 * 把一段文本按 [n] 引用角标拆分为片段序列。
 * 每段要么是纯文本，要么是引用标记。
 * 流式渲染中 [ 未闭合时不解析为引用。
 */
export function splitCitations(text: string): Array<
  | { kind: 'text'; content: string }
  | { kind: 'cite'; marker: number }
> {
  const parts: Array<{ kind: 'text' | 'cite'; content?: string; marker?: number }> = []
  let last = 0
  for (const m of text.matchAll(CITE_RE)) {
    if (m.index! > last) {
      parts.push({ kind: 'text', content: text.slice(last, m.index) })
    }
    parts.push({ kind: 'cite', marker: Number(m[1]) })
    last = m.index! + m[0].length
  }
  if (last < text.length) {
    parts.push({ kind: 'text', content: text.slice(last) })
  }
  return parts as Array<{ kind: 'text'; content: string } | { kind: 'cite'; marker: number }>
}

/** 转义 HTML（纯文本渲染安全红线：innerHTML 一律不使用） */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}
