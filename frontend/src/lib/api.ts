import type { Answer, DocPreview, GraphData, HistoryMessage, UploadDoc } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

export async function postAnswer(params: {
  query: string
  history?: HistoryMessage[]
  response_type?: string
}): Promise<Answer> {
  const res = await fetch(`${API_BASE}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      history: params.history ?? [],
      response_type: params.response_type ?? null,
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

export async function uploadDoc(file: File): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_BASE}/docs`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function listDocs(): Promise<UploadDoc[]> {
  const res = await fetch(`${API_BASE}/docs`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as UploadDoc[]
}

export async function deleteDoc(docId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/docs/${encodeURIComponent(docId)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
}

export async function fetchGraph(docId?: string): Promise<GraphData> {
  const qs = docId ? `?doc_id=${encodeURIComponent(docId)}` : ''
  const res = await fetch(`${API_BASE}/graph${qs}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as GraphData
}

export async function fetchDocPreview(docId: string): Promise<DocPreview> {
  const res = await fetch(`${API_BASE}/docs/${encodeURIComponent(docId)}/preview`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`)
  return (await res.json()) as DocPreview
}
