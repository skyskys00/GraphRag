import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { TopBar } from './components/TopBar'
import { Sidebar } from './components/Sidebar'
import { ChatView } from './components/ChatView'
import { CitationPanel } from './components/CitationPanel'
import { InputBar } from './components/InputBar'
import { EmptyState } from './components/EmptyState'
import { DocumentManager } from './components/DocumentManager'
import { DocumentPreview } from './components/DocumentPreview'
import { useChat } from './hooks/useChat'
import { checkHealth, deleteDoc, listDocs, uploadDoc } from './lib/api'
import { GraphView } from './components/GraphView'
import type { GraphFocus } from './components/GraphView'
import type { Citation, UploadDoc } from './types'

// 开发时可通过 USE_MOCK=true 用离线 mock 流走查（不依赖后端）
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const SUGGESTIONS = [
  '客户投诉的处理流程是怎样的？',
  '智能客服系统近期完成了哪些迭代？',
  '办公用品月度汇总的口径是什么？',
  '季度复盘的核心指标有哪些？',
]

type View = 'chat' | 'documents' | 'graph'
type RightTab = 'citations' | 'preview'

function App() {
  const {
    messages,
    send,
    clear,
    backendOnline,
    setBackendOnline,
    activeCitation,
    setActiveCitation,
    currentCitations,
  } = useChat({ useMock: USE_MOCK })

  const [activeView, setActiveView] = useState<View>('chat')
  const [graphFocus, setGraphFocus] = useState<GraphFocus | null>(null)
  const [docs, setDocs] = useState<UploadDoc[]>([])
  const [uploading, setUploading] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimerRef = useRef<number | undefined>(undefined)

  // 右栏 tab：引用来源 / 预览（预览有选中文档时展示，close 切回引用来源）
  const [rightTab, setRightTab] = useState<RightTab>('citations')
  const [previewDoc, setPreviewDoc] = useState<UploadDoc | null>(null)
  const [previewJump, setPreviewJump] = useState<string | null>(null)

  const bottomRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const chatRef = useRef<HTMLDivElement>(null)

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    window.clearTimeout(toastTimerRef.current)
    toastTimerRef.current = window.setTimeout(() => setToast(null), 4000)
  }, [])

  // 流式过程中自动滚到底（用户主动上翻后暂停跟随）
  useEffect(() => {
    const el = chatRef.current
    if (!el) return
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      autoScrollRef.current = nearBottom
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (autoScrollRef.current && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [messages])

  // 健康探测（每 30s 轮询）
  useEffect(() => {
    if (USE_MOCK) {
      setBackendOnline(true)
      return
    }
    let stop = false
    const tick = async () => {
      const ok = await checkHealth()
      if (!stop) setBackendOnline(ok)
    }
    tick()
    const t = setInterval(tick, 30000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [setBackendOnline])

  const refreshDocs = useCallback(async () => {
    try {
      setDocs(await listDocs())
    } catch {
      // 后端离线时静默，UI 保持上次列表
    }
  }, [])

  useEffect(() => {
    if (!USE_MOCK) refreshDocs()
  }, [refreshDocs])

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      await uploadDoc(file)
      await refreshDocs()
      showToast(`已开始处理「${file.name}」，入库后可对它提问`)
    } catch (err) {
      showToast(`上传失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setUploading(false)
    }
  }

  // 有文档处理中时轮询状态（2s），完成后提示
  const hasProcessing = docs.some((d) => d.status === 'processing')
  const prevProcessingRef = useRef(hasProcessing)
  useEffect(() => {
    if (prevProcessingRef.current && !hasProcessing && !USE_MOCK) {
      const failed = docs.filter((d) => d.status === 'failed')
      if (failed.length > 0) {
        showToast(`${failed.map((d) => d.filename).join('、')} 入库失败，可删除后重试`)
      } else if (docs.length > 0) {
        showToast('文档已入库，可对它提问')
      }
    }
    prevProcessingRef.current = hasProcessing
  }, [hasProcessing, docs, showToast])

  useEffect(() => {
    if (!hasProcessing) return
    const t = setInterval(refreshDocs, 2000)
    return () => clearInterval(t)
  }, [hasProcessing, refreshDocs])

  const handleDelete = async (docId: string) => {
    try {
      await deleteDoc(docId)
      setDocs((prev) => prev.filter((d) => d.doc_id !== docId))
      if (previewDoc?.doc_id === docId) setPreviewDoc(null)
      showToast('文档已删除，不再参与检索')
    } catch (err) {
      showToast(`删除失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const isStreaming = useMemo(
    () => messages.some((m) => m.state === 'streaming' || m.state === 'pending'),
    [messages],
  )

  const handleCitationClick = (marker: number) => {
    setActiveCitation((prev) => (prev === marker ? null : marker))
    // 滚动到对应引用卡
    const el = document.getElementById(`cite-${marker}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  const handleJumpToGraph = (citation: Citation) => {
    setGraphFocus({
      chunkId: citation.text_unit_id || undefined,
      fullDocId: citation.full_doc_id,
      ts: Date.now(),
    })
    setActiveView('graph')
  }

  // 预览：docId 必须、text_unit_id 可选（来自引用的「原文档」跳转定位）
  const openPreview = useCallback(
    (docId: string, jumpUnitId?: string) => {
      const hit = docs.find((d) => d.doc_id === docId)
      setPreviewDoc(hit ?? { doc_id: docId, filename: docId, status: 'ready' })
      setPreviewJump(jumpUnitId ?? null)
      setRightTab('preview')
    },
    [docs],
  )

  // 当前引用里每个文档置信度第一（top1）的片段，供预览高亮
  const topUnitByDoc = useMemo(() => {
    const m = new Map<string, { unitId: string; score: number }>()
    for (const c of currentCitations) {
      const cur = m.get(c.full_doc_id)
      if (!cur || c.score > cur.score) m.set(c.full_doc_id, { unitId: c.text_unit_id, score: c.score })
    }
    return m
  }, [currentCitations])

  return (
    <div className={`app${activeView === 'documents' ? ' docs' : activeView === 'graph' ? ' graph' : ''}`}>
      <TopBar online={backendOnline} onClear={clear} />
      <Sidebar activeView={activeView} onNav={setActiveView} />

      {activeView === 'documents' ? (
        <DocumentManager
          docs={docs}
          onDelete={handleDelete}
          onPreview={openPreview}
          onBack={() => setActiveView('chat')}
        />
      ) : activeView === 'graph' ? (
        <GraphView focus={graphFocus} uploadDocs={docs} />
      ) : messages.length === 0 ? (
        <main className="chat" ref={chatRef}>
          <EmptyState suggestions={SUGGESTIONS} onPick={(q) => send(q)} />
        </main>
      ) : (
        <div ref={chatRef} style={{ gridArea: 'chat', overflowY: 'auto', paddingRight: 20 }}>
          <ChatView
            messages={messages}
            onCitationClick={handleCitationClick}
            activeCitation={activeCitation}
            bottomRef={bottomRef}
          />
        </div>
      )}

      <aside className="cite-panel">
        <div className="cite-tabs" role="tablist">
          <button
            className={`cite-tab${rightTab === 'citations' ? ' active' : ''}`}
            onClick={() => setRightTab('citations')}
          >
            引用来源
          </button>
          <button
            className={`cite-tab${rightTab === 'preview' ? ' active' : ''}`}
            onClick={() => setRightTab('preview')}
          >
            预览
            {previewDoc && <span className="cite-tab-dot" aria-hidden />}
          </button>
        </div>

        {rightTab === 'citations' || !previewDoc ? (
          <CitationPanel
            citations={currentCitations}
            activeMarker={activeCitation}
            onJumpToGraph={handleJumpToGraph}
            onOpenPreview={openPreview}
          />
        ) : (
          <DocumentPreview
            doc={previewDoc}
            jumpUnitId={previewJump}
            topUnitId={topUnitByDoc.get(previewDoc.doc_id ?? '')?.unitId ?? null}
            onClose={() => setRightTab('citations')}
          />
        )}
      </aside>

      {activeView === 'chat' && (
        <InputBar
          onSend={send}
          onUpload={handleUpload}
          disabled={isStreaming || backendOnline === false}
          uploading={uploading}
        />
      )}

      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  )
}

export default App