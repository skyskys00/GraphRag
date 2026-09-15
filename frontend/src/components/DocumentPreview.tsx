import { useEffect, useState } from 'react'
import { fetchDocPreview } from '../lib/api'
import type { DocPreview, UploadDoc } from '../types'

interface DocumentPreviewProps {
  doc: UploadDoc
  jumpUnitId?: string | null
  topUnitId?: string | null
  onClose: () => void
}

type LoadState = 'loading' | 'ready' | 'error'

export function DocumentPreview({ doc, jumpUnitId, topUnitId, onClose }: DocumentPreviewProps) {
  const [state, setState] = useState<LoadState>('loading')
  const [preview, setPreview] = useState<DocPreview | null>(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [attempt, setAttempt] = useState(0)

  const docId = doc.doc_id ?? ''

  useEffect(() => {
    let alive = true
    setState('loading')
    setPreview(null)
    async function load() {
      try {
        const p = await fetchDocPreview(docId)
        if (!alive) return
        setPreview(p)
        setState('ready')
      } catch (e) {
        if (!alive) return
        setErrorMsg(e instanceof Error ? e.message : String(e))
        setState('error')
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [docId, attempt])

  // 定位：引用跳转优先，其次 top1 最高置信度片段；ready 后滚动到该单元
  useEffect(() => {
    if (state !== 'ready') return
    const targetId = jumpUnitId || topUnitId
    if (!targetId) return
    const el = document.getElementById(`pu-${targetId}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [state, jumpUnitId, topUnitId])

  return (
    <div className="preview-panel">
      <div className="preview-head">
        <div className="preview-title" title={doc.filename}>
          {doc.filename}
        </div>
        <button className="link-btn" onClick={onClose}>
          ← 回到引用
        </button>
      </div>
      {preview && (
        <div className="preview-sub">
          {preview.units.length} 个片段
          {topUnitId && ' · 已高亮最高置信度片段'}
        </div>
      )}

      {state === 'loading' && <div className="preview-state">文档预览加载中…</div>}
      {state === 'error' && (
        <div className="preview-state error">
          <p>预览加载失败：{errorMsg}</p>
          <button
            className="link-btn"
            onClick={() => {
              setState('loading')
              setAttempt((a) => a + 1)
            }}
          >
            重试
          </button>
        </div>
      )}

      {state === 'ready' && preview && (
        <div className="preview-list">
          {preview.units.length === 0 && <div className="preview-state">该文档暂无切片内容</div>}
          {preview.units.map((u) => {
            const isTop = u.text_unit_id === topUnitId
            const isJump = jumpUnitId != null && u.text_unit_id === jumpUnitId && !isTop
            return (
              <div
                key={u.text_unit_id}
                id={`pu-${u.text_unit_id}`}
                className={`preview-unit${isTop ? ' is-top' : isJump ? ' is-jump' : ''}`}
              >
                <div className="preview-unit-meta">
                  <span className="preview-unit-id">{u.text_unit_id.split('-chunk-')[1] ?? u.text_unit_id}</span>
                  {isTop && <span className="preview-top1-tag">最高置信度</span>}
                </div>
                {u.title_path && <div className="preview-unit-path">{u.title_path}</div>}
                {u.page_range && (
                  <div className="preview-unit-pages">P{u.page_range[0]}–P{u.page_range[1]}</div>
                )}
                <div className="preview-unit-content">{u.content}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}