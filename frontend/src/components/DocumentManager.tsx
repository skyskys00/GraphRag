import type { UploadDoc } from '../types'

interface DocumentManagerProps {
  docs: UploadDoc[]
  onDelete: (docId: string) => void
  onPreview: (docId: string) => void
  onBack: () => void
}

const STATUS_LABEL: Record<UploadDoc['status'], string> = {
  processing: '处理中…',
  ready: '已入库',
  failed: '失败',
}

export function DocumentManager({ docs, onDelete, onPreview, onBack }: DocumentManagerProps) {
  return (
    <main className="doc-manager">
      <div className="doc-head">
        <h2 className="doc-title">文档管理</h2>
        <button className="link-btn" onClick={onBack}>
          ← 返回问答
        </button>
      </div>

      {docs.length === 0 ? (
        <div className="doc-empty">
          还没有入库的文档。回到问答视图，点击输入框左侧的
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
          按钮上传 pdf / docx / md 文件。
        </div>
      ) : (
        <ul className="doc-list">
          {docs.map((d) => (
            <li key={d.task_id ?? d.doc_id} className="doc-row">
              <div className="doc-main">
                <span className="doc-name">{d.filename}</span>
                <span className="doc-sub">
                  {d.doc_id ? `ID ${d.doc_id}` : '处理中…'}
                  {d.created_at ? ` · ${d.created_at}` : ''}
                </span>
                {d.status === 'failed' && d.error && (
                  <span className="doc-error" role="alert">
                    {d.error}
                  </span>
                )}
              </div>
              <span className={`doc-badge ${d.status}`}>{STATUS_LABEL[d.status]}</span>
              {d.status === 'ready' && d.doc_id && (
                <button className="link-btn" onClick={() => onPreview(d.doc_id!)}>
                  预览
                </button>
              )}
              {d.status !== 'processing' && d.doc_id && (
                <button className="link-btn danger" onClick={() => onDelete(d.doc_id!)}>
                  删除
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="doc-tip">
        上传的文档会自动完成解析 → 切分 → 建图 → 索引入库，处理完成后即可在提问中被检索引用。删除后该文档不再参与检索。
      </p>
    </main>
  )
}