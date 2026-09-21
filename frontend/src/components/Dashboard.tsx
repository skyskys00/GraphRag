import { useEffect, useRef, useState } from 'react'
import { fetchStats } from '../lib/api'
import type { CollectionInfo, DashboardStats, UploadDoc } from '../types'

interface DashboardProps {
  collection: CollectionInfo
  docs: UploadDoc[]
  uploading: boolean
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  onUpload: (files: File[]) => void
  onGoDocuments: () => void
  onGoGraph: () => void
  onAsk: (query: string) => void
}

const STATUS_LABEL: Record<UploadDoc['status'], string> = {
  processing: '处理中…',
  ready: '已入库',
  failed: '失败',
}

function relTime(iso: string): string {
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return ''
  const s = Math.floor((Date.now() - t) / 1000)
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}

export function Dashboard({
  collection,
  docs,
  uploading,
  onRename,
  onDelete,
  onUpload,
  onGoDocuments,
  onGoGraph,
  onAsk,
}: DashboardProps) {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [editing, setEditing] = useState(false)
  const [editValue, setEditValue] = useState(collection.name)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let alive = true
    setStats(null)
    fetchStats(collection.id)
      .then((s) => {
        if (alive) setStats(s)
      })
      .catch(() => {
        if (alive) setStats(null)
      })
    return () => {
      alive = false
    }
  }, [collection.id])

  const docCount = stats?.doc_count ?? 0
  const topDocs = docs.slice(0, 5)

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length > 0) onUpload(files)
    e.target.value = ''
  }

  const renderCards = () => {
    const cards: {
      label: string
      value: number | null
      onClick: () => void
    }[] = [
      { label: '文档数', value: stats?.doc_count ?? null, onClick: onGoDocuments },
      { label: '实体数', value: stats?.node_count ?? null, onClick: onGoGraph },
      { label: '关系数', value: stats?.edge_count ?? null, onClick: onGoGraph },
    ]
    return (
      <div className="dash-cards">
        {cards.map((c) => (
          <button key={c.label} className="dash-card" onClick={c.onClick}>
            <span className="dash-card-num">{c.value ?? '—'}</span>
            <span className="dash-card-label">{c.label}</span>
          </button>
        ))}
      </div>
    )
  }

  return (
    <main className="dash-view">
      <div className="dash-head">
        <div className="dash-title-group">
          {editing ? (
            <input
              className="dash-rename-input"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  if (editValue.trim()) onRename(collection.id, editValue.trim())
                  setEditing(false)
                }
                if (e.key === 'Escape') setEditing(false)
              }}
              onBlur={() => setEditing(false)}
              autoFocus
            />
          ) : (
            <h2 className="dash-title" title={collection.name}>
              {collection.name}
            </h2>
          )}
          <div className="dash-actions">
            {!editing && (
              <button className="link-btn" onClick={() => setEditing(true)}>
                ✎ 重命名
              </button>
            )}
            {collection.id !== 'default' && (
              <button className="link-btn danger" onClick={() => onDelete(collection.id)}>
                删除知识库
              </button>
            )}
          </div>
        </div>
        <p className="dash-sub">当前知识库总览 · 问答与文档操作都限定在当前库内</p>
      </div>

      {renderCards()}

      {docCount === 0 ? (
        <div className="dash-empty">
          <h3>这个知识库还是空的</h3>
          <p>
            上传第一篇文档后，系统会自动完成解析 → 切分 → 建图 → 索引入库，
            完成后即可对库内内容提问。
          </p>
          <button
            className="send-btn"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? '上传中…' : '上传第一篇文档'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.md,.pptx,.txt"
            multiple
            hidden
            onChange={handleFile}
          />
        </div>
      ) : (
        <>
          <div className="dash-cols">
            <section className="dash-sec">
              <h3 className="dash-sec-title">最近问答</h3>
              {stats && stats.recent_queries.length > 0 ? (
                <ul className="dash-recent">
                  {stats.recent_queries.slice(0, 8).map((r, i) => (
                    <li key={`${r.ts}-${i}`}>
                      <button className="dash-recent-item" onClick={() => onAsk(r.query)}>
                        <span className="dash-recent-query">{r.query}</span>
                        <span className="dash-recent-time">{relTime(r.ts)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="dash-sec-empty">暂无提问记录，去问答视图提问吧。</p>
              )}
            </section>

            <section className="dash-sec">
              <div className="dash-sec-head">
                <h3 className="dash-sec-title">文档概览</h3>
                <button className="link-btn" onClick={onGoDocuments}>
                  更多 →
                </button>
              </div>
              {topDocs.length > 0 ? (
                <ul className="dash-docs">
                  {topDocs.map((d) => (
                    <li key={d.task_id ?? d.doc_id} className="dash-doc-row">
                      <span className="dash-doc-name" title={d.filename}>
                        {d.filename}
                      </span>
                      <span className={`doc-badge ${d.status}`}>
                        {STATUS_LABEL[d.status]}
                      </span>
                      <span className="dash-doc-time">
                        {d.created_at ? relTime(d.created_at) : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="dash-sec-empty">暂无已入库文档。</p>
              )}
            </section>
          </div>

          <section className="dash-quick">
            <h3 className="dash-sec-title">快速操作</h3>
            <div className="dash-quick-actions">
              <button
                className="dash-quick-btn"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
              >
                上传文档
              </button>
              <button className="dash-quick-btn" onClick={onGoGraph}>
                进入图谱
              </button>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.md,.pptx,.txt"
              multiple
              hidden
              onChange={handleFile}
            />
          </section>
        </>
      )}
    </main>
  )
}