import type { Root, RootContent, PhrasingContent } from 'mdast'

const CITE_RE = /\[(\d+)\]/g
const HAS_CITE = /\[\d+\]/

/** 把文本节点里的 [n] 拆成 link 节点（url = #cite-n），交给渲染层换成可点击角标。
 *  用 hash 而非自定义协议：react-markdown 的 urlTransform 会过滤未知协议。
 *  代码块/行内代码不处理——那里的 [1] 是原文内容，不是引用。
 */
function splitText(value: string): PhrasingContent[] {
  const out: PhrasingContent[] = []
  let last = 0
  CITE_RE.lastIndex = 0
  for (const m of value.matchAll(CITE_RE)) {
    const idx = m.index!
    if (idx > last) out.push({ type: 'text', value: value.slice(last, idx) })
    out.push({
      type: 'link',
      url: `#cite-${m[1]}`,
      children: [{ type: 'text', value: m[0] }],
    })
    last = idx + m[0].length
  }
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) })
  return out
}

function walk(node: Root | RootContent): void {
  const children = (node as { children?: unknown[] }).children
  if (!Array.isArray(children)) return
  for (let i = 0; i < children.length; i++) {
    const child = children[i] as RootContent
    if (child.type === 'text' && HAS_CITE.test(child.value)) {
      const parts = splitText(child.value)
      children.splice(i, 1, ...parts)
      i += parts.length - 1
    } else if (child.type !== 'code' && child.type !== 'inlineCode') {
      walk(child)
    }
  }
}

export function remarkCitations() {
  return (tree: Root) => walk(tree)
}
