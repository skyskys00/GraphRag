interface EmptyStateProps {
  suggestions: string[]
  onPick: (q: string) => void
}

export function EmptyState({ suggestions, onPick }: EmptyStateProps) {
  return (
    <div className="empty-hint">
      <h2>请问我任何关于文档集的问题</h2>
      <p>支持会议纪要、投诉 SOP、产品需求、季度复盘等中文文档问答，答案带引用，可溯源到原文。</p>
      <div className="suggest-chips">
        {suggestions.map((s) => (
          <span key={s} className="chip" onClick={() => onPick(s)}>
            {s}
          </span>
        ))}
      </div>
    </div>
  )
}
