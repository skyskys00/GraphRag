import type {
  Answer,
  CollectionInfo,
  ConversationDetail,
  ConversationInfo,
  DashboardStats,
  DocPreview,
  GraphData,
  HistoryMessage,
  UploadDoc,
} from '../types'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

// 拼带 collection_id 的 query string；extra 键值非空才追加
function withQuery(path: string, params: Record<string, string | undefined>): string {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') url.searchParams.set(k, v)
  }
  return url.toString()
}

export async function postAnswer(params: {
  query: string
  history?: HistoryMessage[]
  response_type?: string
  collection_id?: string
  conversation_id?: string | null
}): Promise<Answer> {
  const res = await fetch(`${API_BASE}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      history: params.history ?? [],
      response_type: params.response_type ?? null,
      collection_id: params.collection_id ?? 'default',
      conversation_id: params.conversation_id ?? null,
    }),
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${detail}`)
  }
  return (await res.json()) as Answer
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) })
    return res.ok && (await res.json()).status === 'ok'
  } catch {
    return false
  }
}

export async function uploadDoc(file: File, collection_id = 'default'): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  form.append('collection_id', collection_id)
  const res = await fetch(`${API_BASE}/docs`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function listDocs(collection_id = 'default'): Promise<UploadDoc[]> {
  const res = await fetch(withQuery('/docs', { collection_id }))
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as UploadDoc[]
}

export async function deleteDoc(docId: string, collection_id = 'default'): Promise<void> {
  const res = await fetch(withQuery(`/docs/${encodeURIComponent(docId)}`, { collection_id }), {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function fetchGraph(
  params: {
    docId?: string
    level?: 'entity' | 'document'
    collection_id?: string
    /** >0 时请求 PageRank top_n 核心节点（level=entity 有效） */
    top_n?: number
  } = {},
): Promise<GraphData> {
  const { docId, level = 'entity', collection_id = 'default', top_n } = params
  const res = await fetch(
    withQuery('/graph', {
      doc_id: docId,
      level,
      collection_id,
      top_n: top_n && top_n > 0 ? String(top_n) : undefined,
    }),
  )
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as GraphData
}

export async function fetchDocPreview(docId: string, collection_id = 'default'): Promise<DocPreview> {
  const res = await fetch(withQuery(`/docs/${encodeURIComponent(docId)}/preview`, { collection_id }))
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as DocPreview
}

// ---------- M8 v3：知识库 ----------

export async function listCollections(): Promise<CollectionInfo[]> {
  const res = await fetch(`${API_BASE}/collections`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as CollectionInfo[]
}

export async function createCollection(name: string): Promise<{ id: string }> {
  const res = await fetch(withQuery('/collections', { name }), { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as { id: string }
}

export async function renameCollection(id: string, name: string): Promise<void> {
  const res = await fetch(withQuery(`/collections/${encodeURIComponent(id)}`, { name }), {
    method: 'PATCH',
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function deleteCollection(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/collections/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function fetchStats(collection_id = 'default'): Promise<DashboardStats> {
  const res = await fetch(withQuery('/stats', { collection_id }))
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as DashboardStats
}

// ---------- M8 v5 多会话 ----------

export async function listConversations(collection_id = 'default'): Promise<ConversationInfo[]> {
  const res = await fetch(withQuery('/conversations', { collection_id }))
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as ConversationInfo[]
}

export async function createConversation(
  collection_id = 'default',
  title?: string,
): Promise<{ conversation_id: string }> {
  const res = await fetch(withQuery('/conversations', { collection_id, title }), { method: 'POST' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as { conversation_id: string }
}

export async function fetchConversation(
  id: string,
  collection_id = 'default',
): Promise<ConversationDetail> {
  const res = await fetch(withQuery(`/conversations/${encodeURIComponent(id)}`, { collection_id }))
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as ConversationDetail
}

export async function renameConversation(
  id: string,
  title: string,
  collection_id = 'default',
): Promise<void> {
  const res = await fetch(
    withQuery(`/conversations/${encodeURIComponent(id)}`, { collection_id, title }),
    { method: 'PATCH' },
  )
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function deleteConversation(id: string, collection_id = 'default'): Promise<void> {
  const res = await fetch(
    withQuery(`/conversations/${encodeURIComponent(id)}`, { collection_id }),
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}