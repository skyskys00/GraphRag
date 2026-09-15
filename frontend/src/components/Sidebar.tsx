import { useState } from 'react'

type View = 'chat' | 'documents' | 'graph'

// 导航项数组：新模块（如数据分析）只需在 items 里加一项 + 提供对应 View 组件
const NAV_ITEMS: { key: View; label: string; icon: string }[] = [
  { key: 'chat', label: '问答', icon: '◌' },
  { key: 'documents', label: '文档管理', icon: '▤' },
  { key: 'graph', label: '知识图谱', icon: '✳' },
]

interface SidebarProps {
  activeView: View
  onNav: (v: View) => void
}

export function Sidebar({ activeView, onNav }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
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