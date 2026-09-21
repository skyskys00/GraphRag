import { useEffect, useRef, useState } from 'react'
import type { AppView, CollectionInfo, ConversationInfo } from '../types'

// 导航项数组：新模块（如数据分析）只需在 items 里加一项 + 提供对应 View 组件
const NAV_ITEMS: { key: AppView; label: string; icon: string }[] = [
  { key: 'dashboard', label: '仪表盘', icon: '▦' },
  { key: 'chat', label: '问答', icon: '◌' },
  { key: 'documents', label: '文档管理', icon: '▤' },
  { key: 'graph', label: '知识图谱', icon: '✳' },
]

interface SidebarProps {
  activeView: AppView
  onNav: (v: AppView) => void
  collections: CollectionInfo[]
  current: string
  onSwitch: (id: string) => void
  onCreate: (name: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  conversations: ConversationInfo[]
  activeConversationId: string | null
  onSelectConversation: (id: string) => void
  onNewConversation: () => void
  onRenameConversation: (id: string) => void
  onDeleteConversation: (id: string) => void
  conversationsHidden: boolean
  streamingMap?: Record<string, boolean>
}

export function Sidebar({
  activeView,
  onNav,
  collections,
  current,
  onSwitch,
  onCreate,
  onRename,
  onDelete,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  conversationsHidden,
  streamingMap = {},
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [open, setOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [newName, setNewName] = useState('')
  const editRef = useRef<HTMLInputElement>(null)

  const currentName = collections.find((c) => c.id === current)?.name ?? current

  // 点击切换器外部关闭下拉
  useEffect(() => {
    if (!open) return
    const onUp = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('[data-switcher]')) setOpen(false)
    }
    document.addEventListener('click', onUp)
    return () => document.removeEventListener('click', onUp)
  }, [open])

  useEffect(() => {
    if (editingId && editRef.current) editRef.current.focus()
  }, [editingId])

  const startRename = (c: CollectionInfo) => {
    setEditingId(c.id)
    setEditValue(c.name)
  }
  const commitRename = () => {
    if (editingId && editValue.trim()) onRename(editingId, editValue.trim())
    setEditingId(null)
  }

  const handleCreate = () => {
    if (!newName.trim()) return
    onCreate(newName.trim())
    setNewName('')
    setOpen(false)
  }

  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <button
        className="sidebar-toggle"
        onClick={() => setCollapsed((c) => !c)}
        title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
      >
        {collapsed ? '»' : '«'}
      </button>

      {!collapsed && (
        <div className="collection-switcher" data-switcher>
          <button
            className="collection-switch-btn"
            onClick={() => setOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={open}
            title="切换 / 新建 / 管理知识库"
          >
            <span className="collection-switch-name">{currentName}</span>
            <span className="collection-switch-arrow">{open ? '▴' : '▾'}</span>
          </button>

          {open && (
            <div className="collection-menu" role="menu">
              <div className="collection-menu-tip">全部知识库</div>
              {collections.map((c) => (
                <div
                  key={c.id}
                  className={`collection-menu-row${c.id === current ? ' current' : ''}`}
                >
                  {editingId === c.id ? (
                    <input
                      ref={editRef}
                      className="collection-rename-input"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename()
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      onBlur={commitRename}
                    />
                  ) : (
                    <button
                      className="collection-menu-name"
                      onClick={() => {
                        onSwitch(c.id)
                        setOpen(false)
                      }}
                    >
                      <span className="collection-menu-label">{c.name}</span>
                      <span className="collection-menu-count">{c.doc_count}</span>
                    </button>
                  )}
                  {c.id !== 'default' && editingId !== c.id && (
                    <span className="collection-menu-actions">
                      <button
                        className="collection-menu-act"
                        onClick={() => startRename(c)}
                        title="重命名"
                        aria-label={`重命名 ${c.name}`}
                      >
                        ✎
                      </button>
                      <button
                        className="collection-menu-act danger"
                        onClick={() => onDelete(c.id)}
                        title="删除"
                        aria-label={`删除 ${c.name}`}
                      >
                        ×
                      </button>
                    </span>
                  )}
                </div>
              ))}

              <div className="collection-menu-create">
                <input
                  className="collection-create-input"
                  placeholder="新建知识库名称…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                />
                <button className="collection-create-btn" onClick={handleCreate}>
                  新建
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!collapsed && !conversationsHidden && (
        <div className="conversations-section">
          <div className="conversations-head">
            <span className="conversations-title">对话</span>
            <button
              className="conversations-new"
              onClick={onNewConversation}
              title="新对话"
              aria-label="新对话"
            >
              ＋
            </button>
          </div>
          <div className="conversations-list">
            {conversations.length === 0 ? (
              <div className="conversations-empty">暂无对话，直接提问即可开启</div>
            ) : (
              conversations.map((c) => (
                <div
                  key={c.conversation_id}
                  className={`conversation-row${
                    c.conversation_id === activeConversationId ? ' active' : ''
                  }`}
                >
                  <button
                    className="conversation-name"
                    onClick={() => onSelectConversation(c.conversation_id)}
                    title={c.title}
                  >
                    {streamingMap[c.conversation_id] && (
                      <span className="conv-streaming-dot" title="生成中" />
                    )}
                    {c.title}
                  </button>
                  <span className="conversation-actions">
                    <button
                      className="conversation-act"
                      onClick={() => onRenameConversation(c.conversation_id)}
                      title="重命名"
                      aria-label="重命名对话"
                    >
                      ✎
                    </button>
                    <button
                      className="conversation-act danger"
                      onClick={() => onDeleteConversation(c.conversation_id)}
                      title="删除"
                      aria-label="删除对话"
                    >
                      ×
                    </button>
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.key}
            className={`sidebar-item${activeView === item.key ? ' active' : ''}`}
            onClick={() => onNav(item.key)}
            title={collapsed ? item.label : undefined}
          >
            <span className="sidebar-icon" aria-hidden>
              {item.icon}
            </span>
            {!collapsed && <span className="sidebar-label">{item.label}</span>}
          </button>
        ))}
      </nav>
    </aside>
  )
}