import type { Citation } from '../types'

interface CitationPanelProps {
  citations: Citation[]
  activeMarker: number | null
  onJumpToGraph: (c: Citation) => void
  onOpenPreview: (docId: string, textUnitId?: string) => void
}

const MAX_CITATIONS = 5

export function CitationPanel({ citations, activeMarker, onJumpToGraph, onOpenPreview }: CitationPanelProps) {
  // 按置信度降序展示，最多前 5 条；marker 保留回答正文中的原始编号
  const top = [...citations].sort((a, b) => b.score - a.score).slice(0, MAX_CITATIONS)
  const truncated = citations.length > top.length

  return (
    <>
      <div className="cite-head">
        <h3>引用来源</h3>
        <span className="cite-count">
          {truncated ? '前 ' + top.length + ' 条' : citations.length ? `${citations.length} 条` : '—'}
        </span>
      </div>
      <div className="cite-list">
        {citations.length === 0 && <p className="cite-empty">发送问题后将展示引用来源</p>}
        {truncated && (
          <p className="cite-more" title="按置信度取前 5 条">
            共 {citations.length} 条，仅展示置信度最高的 {MAX_CITATIONS} 条
          </p>
        )}
        {top.map((c) => (
          <div
            key={c.marker}
            id={`cite-${c.marker}`}
            className={`cite-item ${activeMarker === c.marker ? 'active' : ''}`}
          >
            <span className="cite-marker">{c.marker}</span>
            <div className="cite-title">{c.file_path || c.full_doc_id}</div>
            <div className="cite-meta">
              {c.page_range
                ? `P${c.page_range[0]}–P${c.page_range[1]}`
                : c.title_path
                  ? c.title_path
                  : '—'}
              {' · '}相关度 {Math.round(c.score * 100)}%
            </div>
            <div className="cite-snippet">{c.snippet}</div>
            <div className="cite-score">
              <span>置信度</span>
              <span className="bar">
                <span style={{ width: `${c.score * 100}%` }} />
              </span>
            </div>
            <div className="cite-actions">
              <button
                className="cite-graph-link"
                onClick={() => onJumpToGraph(c)}
                title="在图谱中定位该引用涉及的实体"
              >
                在图谱中查看 ↗
              </button>
              <button
                className="cite-preview-link"
                onClick={() => onOpenPreview(c.full_doc_id, c.text_unit_id)}
                title="在预览中打开原文档"
              >
                原文档 #
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}