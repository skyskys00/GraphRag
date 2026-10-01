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
import { Dashboard } from './components/Dashboard'
import { useChat } from './hooks/useChat'
import {
  checkHealth,
  createCollection,
  deleteCollection,
  deleteDoc,
  listCollections,
  listDocs,
  renameCollection,
  uploadDoc,
} from './lib/api'
import { GraphView } from './components/GraphView'
import type { GraphFocus } from './components/GraphView'
import type { AppView, Citation, CollectionInfo, UploadDoc } from './types'

// 开发时可通过 USE_MOCK=true 用离线 mock 流走查（不依赖后端）
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const COLLECTION_KEY = 'graphrag.collection.v1'
const SUGGESTIONS: string[] = []

type RightTab = 'citations' | 'preview'

function loadCollection(): string {
  try {
    const v = localStorage.getItem(COLLECTION_KEY)
    return v && v !== 'null' && v !== 'undefined' ? v : 'default'
  } catch {
    return 'default'
  }
}

function App() {
  const {
    messages,
    send,
    conversations,
    currentConversationId,
    loadConversations,
    selectConversation,
    newConversation,
    renameConversation,
    deleteConversation,
    backendOnline,
    setBackendOnline,
    streamingMap,
    isStreaming: isConvStreaming,
    activeCitation,
    setActiveCitation,
    currentCitations,
  } = useChat({ useMock: USE_MOCK })

  const [activeView, setActiveView] = useState<AppView>('dashboard')
  const [graphFocus, setGraphFocus] = useState<GraphFocus | null>(null)
  const [docs, setDocs] = useState<UploadDoc[]>([])
  const [uploading, setUploading] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimerRef = useRef<number | undefined>(undefined)

  // 右栏 tab：引用来源 / 预览（预览有选中文档时展示，close 切回引用来源）
  const [rightTab, setRightTab] = useState<RightTab>('citations')
  const [previewDoc, setPreviewDoc] = useState<UploadDoc | null>(null)
  const [previewJump, setPreviewJump] = useState<string | null>(null)

  // 多知识库：当前集合 + 全量列表
  const [current, setCurrent] = useState<string>(loadCollection)
  const [collections, setCollections] = useState<CollectionInfo[]>([
    { id: 'default', name: '默认知识库', created_at: '', doc_count: 0 },
  ])

  const bottomRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const chatRef = useRef<HTMLDivElement>(null)

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    window.clearTimeout(toastTimerRef.current)
    toastTimerRef.current = window.setTimeout(() => setToast(null), 4000)
  }, [])

  const persistCurrent = useCallback((id: string) => {
    try {
      localStorage.setItem(COLLECTION_KEY, id)
    } catch {
      // 隐私模式 / 配额满时静默
    }
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

  // 加载知识库列表；current 校验，已删除则回落 default
  const loadCollections = useCallback(async () => {
    try {
      const list = await listCollections()
      setCollections(list)
      return list
    } catch {
      return null
    }
  }, [])

  useEffect(() => {
    if (USE_MOCK) return
    let alive = true
    loadCollections().then((list) => {
      if (!alive || !list) return
      const ids = new Set(list.map((c) => c.id))
      if (!ids.has(current)) {
        setCurrent('default')
        persistCurrent('default')
        showToast('所在知识库已被删除，已切换到默认知识库')
      }
    })
    return () => {
      alive = false
    }
    // 仅 mount 时校验一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshDocs = useCallback(async () => {
    try {
      setDocs(await listDocs(current))
    } catch {
      // 后端离线时静默，UI 保持上次列表
    }
  }, [current])

  useEffect(() => {
    if (!USE_MOCK) refreshDocs()
  }, [refreshDocs])

  const handleUpload = async (files: File[]) => {
    if (files.length === 0) return
    setUploading(true)
    try {
      for (const file of files) {
        await uploadDoc(file, current)
      }
      await refreshDocs()
      await loadCollections()
      showToast(`已开始处理 ${files.length} 个文件，入库后可对它提问`)
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
      void loadCollections()
    }
    prevProcessingRef.current = hasProcessing
  }, [hasProcessing, docs, showToast, loadCollections])

  useEffect(() => {
    if (!hasProcessing) return
    const t = setInterval(refreshDocs, 2000)
    return () => clearInterval(t)
  }, [hasProcessing, refreshDocs])

  const handleDelete = async (docId: string) => {
    try {
      await deleteDoc(docId, current)
      setDocs((prev) => prev.filter((d) => d.doc_id !== docId))
      if (previewDoc?.doc_id === docId) setPreviewDoc(null)
      await loadCollections()
      showToast('文档已删除，不再参与检索')
    } catch (err) {
      showToast(`删除失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const isStreaming = isConvStreaming(currentConversationId)

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

  // ---------- 知识库操作 ----------

  const handleSwitch = (id: string) => {
    if (id === current) return
    setCurrent(id)
    persistCurrent(id)
    setGraphFocus(null)
    setPreviewDoc(null)
    setRightTab('citations')
  }

  // 切库后加载该库会话列表并选中最近一个（无会话则清空当前消息）
  useEffect(() => {
    if (USE_MOCK) return
    let alive = true
    loadConversations(current).then((list) => {
      if (!alive) return
      void selectConversation(list.length > 0 ? list[0].conversation_id : null, current)
    })
    return () => {
      alive = false
    }
  }, [current, loadConversations, selectConversation])

  // ---------- 多对话操作 ----------

  const handleNewChat = async () => {
    setActiveView('chat')
    try {
      await newConversation(current)
    } catch (err) {
      showToast(`新建对话失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleConvRename = async (id: string) => {
    const cur = conversations.find((c) => c.conversation_id === id)
    const title = window.prompt('重命名对话', cur?.title ?? '')
    if (title == null) return
    const t = title.trim()
    if (!t) return
    try {
      await renameConversation(id, t, current)
      showToast('已重命名')
    } catch (err) {
      showToast(`重命名失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleConvDelete = async (id: string) => {
    if (!window.confirm('删除该对话？其消息记录将不可恢复。')) return
    try {
      await deleteConversation(id, current)
      showToast('对话已删除')
    } catch (err) {
      showToast(`删除失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleCreate = async (name: string) => {
    try {
      const { id } = await createCollection(name)
      const list = await loadCollections()
      setCurrent(id)
      persistCurrent(id)
      setActiveView('dashboard')
      showToast(`已创建「${name}」，上传文档即可使用`)
      return list
    } catch (err) {
      showToast(`创建失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleRename = async (id: string, name: string) => {
    try {
      await renameCollection(id, name)
      await loadCollections()
      showToast('已重命名')
    } catch (err) {
      showToast(`重命名失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const handleDeleteCollection = async (id: string) => {
    const col = collections.find((c) => c.id === id)
    if (!col || id === 'default') return
    const ok = window.confirm(
      `删除知识库「${col.name}」将删除其下全部文档、TextUnit、图谱与索引数据，且不可恢复。确定继续？`,
    )
    if (!ok) return
    try {
      await deleteCollection(id)
      await loadCollections()
      if (id === current) {
        setCurrent('default')
        persistCurrent('default')
        setActiveView('dashboard')
        setGraphFocus(null)
        setPreviewDoc(null)
      }
      showToast('知识库已删除')
    } catch (err) {
      showToast(`删除失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const currentCollection =
    collections.find((c) => c.id === current) ??
    ({ id: current, name: current, created_at: '', doc_count: 0 } as CollectionInfo)

  return (
    <div
      className={`app${
        activeView === 'dashboard'
          ? ' dashboard'
          : activeView === 'documents'
            ? ' docs'
            : activeView === 'graph'
              ? ' graph'
              : ''
      }`}
    >
      <TopBar online={backendOnline} onNew={handleNewChat} newDisabled={isStreaming} />
      <Sidebar
        activeView={activeView}
        onNav={setActiveView}
        collections={collections}
        current={current}
        onSwitch={handleSwitch}
        onCreate={handleCreate}
        onRename={handleRename}
        onDelete={handleDeleteCollection}
        conversations={conversations}
        activeConversationId={currentConversationId}
        onSelectConversation={(id) => {
          setActiveView('chat')
          void selectConversation(id, current)
        }}
        onNewConversation={handleNewChat}
        onRenameConversation={handleConvRename}
        onDeleteConversation={handleConvDelete}
        conversationsHidden={USE_MOCK}
        streamingMap={streamingMap}
      />

      {activeView === 'dashboard' ? (
        <Dashboard
          collection={currentCollection}
          docs={docs}
          uploading={uploading}
          onRename={handleRename}
          onDelete={handleDeleteCollection}
          onUpload={handleUpload}
          onGoDocuments={() => setActiveView('documents')}
          onGoGraph={() => setActiveView('graph')}
          onAsk={(q) => {
            setActiveView('chat')
            send(q, undefined, current)
          }}
        />
      ) : activeView === 'documents' ? (
        <DocumentManager
          docs={docs}
          onDelete={handleDelete}
          onPreview={openPreview}
          onBack={() => setActiveView('chat')}
        />
      ) : activeView === 'graph' ? (
        <GraphView focus={graphFocus} uploadDocs={docs} collectionId={current} />
      ) : messages.length === 0 ? (
        <main className="chat" ref={chatRef}>
          <EmptyState suggestions={SUGGESTIONS} onPick={(q) => send(q, undefined, current)} />
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

      {activeView !== 'dashboard' && (
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
              collectionId={current}
              onJumpToGraph={handleJumpToGraph}
              onOpenPreview={openPreview}
            />
          ) : (
            <DocumentPreview
              doc={previewDoc}
              jumpUnitId={previewJump}
              topUnitId={topUnitByDoc.get(previewDoc.doc_id ?? '')?.unitId ?? null}
              collectionId={current}
              onClose={() => setRightTab('citations')}
            />
          )}
        </aside>
      )}

      {activeView === 'chat' && (
        <InputBar
          onSend={(q, rt) => send(q, rt, current)}
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