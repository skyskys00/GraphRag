interface TopBarProps {
  online: boolean | null
  onClear: () => void
}

export function TopBar({ online, onClear }: TopBarProps) {
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
        <button className="link-btn" onClick={onClear}>
          清空会话
        </button>
      </div>
    </header>
  )
}