interface TopBarProps {
  online: boolean | null
  onNew: () => void
  newDisabled?: boolean
}

export function TopBar({ online, onNew, newDisabled = false }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="dot" />
        GraphRAG · 知识问答
      </div>
      <div className="top-actions">
        <span className="status-pill">
          <span className={`led ${online === false ? 'offline' : ''}`} />
          {online === null ? '检测中…' : online ? '后端在线' : '后端离线'}
        </span>
        <button className="link-btn" onClick={onNew} disabled={newDisabled}>
          新对话
        </button>
      </div>
    </header>
  )
}