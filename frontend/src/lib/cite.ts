/** 转义 HTML（纯文本渲染安全红线：innerHTML 一律不使用） */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}
