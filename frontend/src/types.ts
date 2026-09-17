// M7 后端契约镜像。字段逐一对应 app/m7_interact + app/m6_generate/cite.py。
// retrieval 字段不做渲染（可能含 numpy 标量，序列化不可靠），来源统一看 meta.source_docs 与 citations。

export interface Citation {
  marker: number
  text_unit_id: string
  full_doc_id: string
  file_path: string
  title_path: string | null
  page_range: number[] | null
  anchor: string | null
  snippet: string
  score: number
}

export interface AnswerMeta {
  mode: string
  used_chunks: number
  context_tokens?: number
  unmatched_markers?: number
  source_docs: string[]
}

export interface Answer {
  query: string
  text: string
  citations: Citation[]
  retrieval?: unknown
  meta: AnswerMeta
}

// SSE 事件线：retrieved → delta ×N → citations → done（异常时 error 结束）
export type SseEvent =
  | { type: 'retrieved'; data: AnswerMeta }
  | { type: 'delta'; data: { text: string } }
  | { type: 'citations'; data: { citations: Citation[]; meta: AnswerMeta } }
  | { type: 'done'; data: { text: string; query: string } }
  | { type: 'error'; data: { message: string } }

export interface HistoryMessage {
  role: 'user' | 'assistant'
  content: string
}

// M7 文档管理契约镜像（POST /docs、GET /docs、DELETE /docs/{doc_id}）
export type DocStatus = 'processing' | 'ready' | 'failed'

export interface UploadDoc {
  task_id?: string
  doc_id: string | null
  filename: string
  status: DocStatus
  error?: string | null
  created_at?: string
}

// 前端会话内单条消息状态
export type MessageState = 'pending' | 'streaming' | 'complete' | 'error'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  state: MessageState
  citations?: Citation[]
  meta?: AnswerMeta
  error?: string
}

// M8 图谱契约镜像（GET /graph；服务端已按软删文档过滤）
export interface GraphNode {
  id: string
  entity_type: string
  description: string
  docs: string[]
  chunks: string[]
}

export interface GraphEdge {
  source: string
  target: string
  relation: string
  weight: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  meta: { node_count: number; edge_count: number }
}

// M8 v2.3 文档全文预览契约镜像（GET /docs/{doc_id}/preview）
export interface PreviewUnit {
  text_unit_id: string
  content: string
  title_path: string | null
  page_range: number[] | null
  file_path: string | null
}

export interface DocPreview {
  doc_id: string
  filename: string
  units: PreviewUnit[]
}

// M8 v3 多知识库（Collection）契约镜像（GET/POST/PATCH/DELETE /collections）
export interface CollectionInfo {
  id: string
  name: string
  created_at: string
  doc_count: number
}

export interface RecentQuery {
  query: string
  ts: string
}

// M8 v3 仪表盘契约镜像（GET /stats）
export interface DashboardStats {
  doc_count: number
  node_count: number
  edge_count: number
  recent_queries: RecentQuery[]
}

// 主区视图：仪表盘 / 问答 / 文档管理 / 知识图谱
export type AppView = 'dashboard' | 'chat' | 'documents' | 'graph'
