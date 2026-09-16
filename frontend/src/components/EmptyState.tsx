interface EmptyStateProps {
  suggestions: string[]
  onPick: (q: string) => void
}

export function EmptyState({ suggestions, onPick }: EmptyStateProps) {
  return (
    <div className="empty-hint">
      <h2>请问我任何关于文档集的问题</h2>
      <p>支持 PDF、DOCX、MD、PPTX、TXT 等格式文档上传，答案带引用，可溯源到原文。</p>
      {suggestions.length > 0 && (
        <div className="suggest-chips">
          {suggestions.map((s) => (
            <span key={s} className="chip" onClick={() => onPick(s)}>
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
