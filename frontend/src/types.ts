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
  image_path: string | null
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
  /** 7 大类归一化类型（组织/人物/产品项目/概念/事件/地点/其他），供类型过滤 chip */
  entity_group: string
  description: string
  docs: string[]
  chunks: string[]
  /** top_n>0 且节点≥40 时才计算（服务端按 PageRank 取 top_n） */
  pagerank?: number
}

export interface GraphEdge {
  source: string
  target: string
  relation: string
  weight: number
  rel_type: number
  rel_type_name: string
}

export interface EntityGraphData {
  level: 'entity'
  nodes: GraphNode[]
  edges: GraphEdge[]
  meta: { node_count: number; edge_count: number }
}

// M8 v4.0 文档级图谱（level=document）
export interface DocGraphNode {
  id: string
  label: string
  entity_count: number
  cluster_id: number
  created_at: string
}

export interface DocGraphEdge {
  source: string
  target: string
  type: 'concept' | 'citation'
  weight: number
  shared_entities?: string[]
  snippet?: string
}

export interface DocCluster {
  id: number
  name: string
  doc_count: number
  color: string
}

export interface DocumentGraphData {
  level: 'document'
  nodes: DocGraphNode[]
  edges: DocGraphEdge[]
  clusters: DocCluster[]
  meta: { node_count: number; edge_count: number; cluster_count: number }
}

export type GraphData = EntityGraphData | DocumentGraphData

// M8 v2.3 文档全文预览契约镜像（GET /docs/{doc_id}/preview）
export interface PreviewUnit {
  text_unit_id: string
  content: string
  title_path: string | null
  page_range: number[] | null
  file_path: string | null
  block_type?: string
  html?: string | null
  image_url?: string | null
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

// M8 v5 多会话契约镜像（GET/POST/PATCH/DELETE /conversations）
export interface ConversationInfo {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  preview: string
}

export interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
  ts?: string
  citations?: Citation[] | null
  meta?: AnswerMeta | null
}

export interface ConversationDetail {
  conversation_id: string
  title: string
  messages: ConversationMessage[]
}

// 器械场景：跨型号参数对比（见 docs/modules/DEVICE_SCENARIO.md §5.1）
export interface CompareSnippet {
  content: string
  score: number
  page_range: number[] | null
  block_type: string | null
  image_url: string | null
}

export interface CompareRow {
  doc_id: string
  doc_name: string
  error?: string
  snippets: CompareSnippet[]
}

export interface CompareResult {
  query: string
  rows: CompareRow[]
  score_cutoff: number
}

// 主区视图：仪表盘 / 问答 / 文档管理 / 知识图谱 / 参数对比
export type AppView = 'dashboard' | 'chat' | 'documents' | 'graph' | 'compare'
