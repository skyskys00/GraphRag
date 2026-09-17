import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Graph } from '@antv/g6'
import type { Element as GElement } from '@antv/g'
import type {
  EdgeData as G6EdgeData,
  GraphOptions,
  IPointerEvent,
  NodeData as G6NodeData,
} from '@antv/g6'
import { fetchGraph } from '../lib/api'
import type { GraphData, UploadDoc } from '../types'

/**
 * 「引用 → 图谱」联动入参：chunkId 优先（引用卡溯源到具体文本片断），
 * 无则退回 fullDocId 反查所属文档的所有实体。ts 用于触发重复跳转。
 */
export interface GraphFocus {
  chunkId?: string
  fullDocId?: string
  ts: number
}

interface GraphViewProps {
  focus?: GraphFocus | null | undefined
  uploadDocs: UploadDoc[]
  collectionId: string
}

type LoadState = 'loading' | 'ready' | 'error'

interface NodeInfo {
  entityType: string
  description: string
  docs: string[]
  chunks: string[]
}

// 浅暖色系映射；未知类型给中性灰（接入任何新实体类型都安全兜底）
const TYPE_COLORS: Record<string, string> = {
  concept: '#f4b943',
  artifact: '#e2825a',
  organization: '#92c47c',
  event: '#f29b9b',
  method: '#c9a6e0',
  person: '#e8a05b',
  data: '#7fc4b0',
  location: '#b8c25e',
  content: '#d1a864',
  product: '#e88bb1',
  task: '#8bb8e0',
  metric: '#9ec57f',
  other: '#aab0b8',
  unknown: '#aab0b8',
}
const DEFAULT_FILL = '#b3bac4'

function trunc(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n)}…` : s
}

export function GraphView({ focus, uploadDocs, collectionId }: GraphViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const prevFocusedRef = useRef<Set<string>>(new Set())
  const prevSelectedRef = useRef<string | null>(null)

  const [graphReady, setGraphReady] = useState(false)

  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [attempt, setAttempt] = useState(0)
  const [errorMsg, setErrorMsg] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [focusInfo, setFocusInfo] = useState<{ hits: number; zone: number } | null>(null)
  const [filterDoc, setFilterDoc] = useState<string>('')

  const rawDataRef = useRef<GraphData | null>(null)
  const infoByIdRef = useRef<Map<string, NodeInfo>>(new Map())
  const graphRenderPRef = useRef<Promise<void> | null>(null)

  // 数据版本：filterDoc 拉回新数据后 +1，驱动图渲染重建
  const [dataVersion, setDataVersion] = useState(0)

  const docNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const d of uploadDocs) if (d.doc_id) m.set(d.doc_id, d.filename)
    return m
  }, [uploadDocs])

  useEffect(() => {
    let alive = true
    // 切换文档维度时清掉旧的焦点/选中态（数据即将整体替换）
    setErrorMsg('')
    setSelectedId(null)
    setFocusInfo(null)
    async function load() {
      try {
        const raw = await fetchGraph(filterDoc || undefined, collectionId)
        if (!alive) return
        rawDataRef.current = raw
        infoByIdRef.current = new Map(raw.nodes.map((n) => [n.id, {
          entityType: n.entity_type,
          description: n.description,
          docs: n.docs,
          chunks: n.chunks,
        }]))
        setDataVersion((v) => v + 1)
        setLoadState('ready')
      } catch (e) {
        if (!alive) return
        setErrorMsg(e instanceof Error ? e.message : String(e))
        setLoadState('error')
      }
    }
    void load()
    return () => {
      alive = false
    }
    // filterDoc 变化 → 按文档重新拉取；attempt 变化即重试（错误态点「重试」）
  }, [filterDoc, attempt])

  // 加载完成 → 建图渲染（dataVersion 变化即按新过滤文档重建）；渲染结束后应用挂起的引用聚焦
  useEffect(() => {
    if (loadState !== 'ready') return
    const el = containerRef.current
    const raw = rawDataRef.current
    if (!el || !raw) return

    const g6Nodes: G6NodeData[] = raw.nodes.map((n) => ({
      id: n.id,
      style: {
        size: 30,
        fill: TYPE_COLORS[n.entity_type] ?? DEFAULT_FILL,
        stroke: '#ffffff',
        lineWidth: 2,
        labelText: trunc(n.id, 14),
        labelPlacement: 'bottom',
        labelFontSize: 11,
        labelFill: '#6b7480',
      },
      data: {
        entityType: n.entity_type,
        description: n.description,
        docs: n.docs,
        chunks: n.chunks,
      },
    }))

    const g6Edges: G6EdgeData[] = raw.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      style: {
        stroke: '#ccd3dc',
        lineWidth: Math.min(1 + (e.weight > 0 ? Math.log2(1 + e.weight) : 0), 3.5),
        // weight 较强的关联才标边名，避免整张网被文本淹没
        labelText: e.relation && e.weight >= 2 ? trunc(e.relation, 10) : undefined,
        labelPlacement: 'center',
        labelFontSize: 10,
        labelFill: '#99a2b0',
      },
    }))

    const options: GraphOptions = {
      container: el,
      data: { nodes: g6Nodes, edges: g6Edges },
      autoFit: { type: 'view' },
      layout: {
        type: 'force',
        preventOverlap: true,
        linkDistance: 160,
      },
      node: {
        state: {
          focused: { fill: '#f6b93b', stroke: '#e67e22', lineWidth: 3 },
          selected: {
            halo: true,
            haloLineWidth: 18,
            haloStroke: 'rgba(246,185,59,0.35)',
            stroke: '#e67e22',
            lineWidth: 4,
          },
        },
      },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element'],
    }

    const graph = new Graph(options)
    graphRef.current = graph
    // 只等初始布局这一次，applyFocus 复用它；destroy 后 reject 也吞掉
    graphRenderPRef.current = graph.render().catch(() => undefined)
    setGraphReady(true)
    return () => {
      graph.destroy()
      graphRef.current = null
      graphRenderPRef.current = null
      setGraphReady(false)
      prevFocusedRef.current = new Set()
      prevSelectedRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- applyFocus 仅用 refs，稳定
  }, [loadState, dataVersion])

  const applyFocus = useCallback(async (graph: Graph, f: GraphFocus) => {
    const info = infoByIdRef.current
    const raw = rawDataRef.current
    if (!raw || info.size === 0) return

    const hitIds = new Set<string>()
    for (const [id, n] of info) {
      const byChunk = f.chunkId ? n.chunks.includes(f.chunkId) : false
      const byDoc = f.fullDocId ? n.docs.includes(f.fullDocId) : false
      if (byChunk || byDoc) hitIds.add(id)
    }
    const zoneIds = new Set<string>(hitIds)
    for (const e of raw.edges) {
      if (hitIds.has(e.source)) zoneIds.add(e.target)
      if (hitIds.has(e.target)) zoneIds.add(e.source)
    }

    // 等初始布局 settle 后再读完焦点，避免二次 render 卡住
    const renderP = graphRenderPRef.current
    if (renderP) await renderP
    prevFocusedRef.current.forEach((id) => void graph.setElementState(id, []))
    prevFocusedRef.current = zoneIds
    zoneIds.forEach((id) => void graph.setElementState(id, ['focused']))

    if (hitIds.size > 0) {
      await graph.focusElement([...hitIds], { duration: 300 })
      setFocusInfo({ hits: hitIds.size, zone: zoneIds.size })
    } else {
      setFocusInfo(null)
    }
  }, [])

  useEffect(() => {
    if (!focus) {
      setFocusInfo(null)
      return
    }
    // 图未建好时不处理，避免 StrictMode 双挂载时在废弃实例上误 apply
    const graph = graphReady ? graphRef.current : null
    if (graph) void applyFocus(graph, focus)
  }, [focus, graphReady, applyFocus])

  // 点击节点 → 详情卡 + selected 高亮（不覆盖引用聚焦的 focused 状态）
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    const onClick = (evt: IPointerEvent) => {
      const id = String((evt.target as GElement).id)
      setSelectedId(id)
    }
    graph.on('node:click', onClick)
    return () => {
      graph.off('node:click', onClick)
    }
  }, [dataVersion])

  // 详情卡问询节点 → selected 高亮（单节点，独立于引用聚焦）
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    void graph.render().then(() => {
      if (prevSelectedRef.current) {
        graph.setElementState(prevSelectedRef.current, [])
        prevSelectedRef.current = null
      }
      if (selectedId) {
        graph.setElementState(selectedId, ['selected'])
        prevSelectedRef.current = selectedId
      }
    })
  }, [selectedId])

  const selected = selectedId ? infoByIdRef.current.get(selectedId) : undefined
  const raw = rawDataRef.current
  const related = useMemo(() => {
    if (!selectedId || !raw) return []
    return raw.edges
      .filter((e) => e.source === selectedId || e.target === selectedId)
      .map((e) => ({
        relation: e.relation,
        weight: e.weight,
        other: e.source === selectedId ? e.target : e.source,
      }))
  }, [selectedId, raw])

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <div className="graph-filter-group">
          <label className="graph-filter-label" htmlFor="graph-doc-filter">
            按文档过滤
          </label>
          <select
            id="graph-doc-filter"
            className="graph-filter-select"
            value={filterDoc}
            onChange={(e) => setFilterDoc(e.target.value)}
          >
            <option value="">全部文档</option>
            {uploadDocs
              .filter((d): d is UploadDoc & { doc_id: string } => !!d.doc_id)
              .map((d) => (
                <option key={d.doc_id} value={d.doc_id}>
                  {d.filename}
                </option>
              ))}
          </select>
        </div>
        {loadState === 'ready' && raw && (
          <span className="graph-count">
            {filterDoc ? `该文档关联 ` : ''}
            {raw.meta.node_count} 节点 · {raw.meta.edge_count} 边
          </span>
        )}
      </div>
      <div className="graph-body">
        <div className="graph-stage">
          <div ref={containerRef} className="graph-canvas" />
        {loadState === 'loading' && <div className="graph-hint">图谱加载中…</div>}
        {loadState === 'error' && (
          <div className="graph-hint error">
            <p>图谱加载失败：{errorMsg}</p>
            <button
              onClick={() => {
                setLoadState('loading')
                setErrorMsg('')
                setAttempt((a) => a + 1)
              }}
            >
              重试
            </button>
          </div>
        )}
        {loadState === 'ready' && focusInfo && (
          <div className="graph-focus-bar" role="status">
            已定位引用相关实体 {focusInfo.hits} 个，含 1 跳邻域共 {focusInfo.zone} 个节点
          </div>
        )}
      </div>

      {selected && (
        <aside className="graph-detail">
          <div className="graph-detail-head">
            <span className="graph-type-tag">{selected.entityType}</span>
            <button className="graph-close" onClick={() => setSelectedId(null)} aria-label="关闭详情">
              ×
            </button>
          </div>
          <h3 className="graph-detail-title">{selectedId}</h3>
          {selected.description ? (
            <p className="graph-detail-desc">{selected.description}</p>
          ) : (
            <p className="graph-detail-desc empty">（该实体无描述）</p>
          )}
          <div className="graph-detail-sec">
            <h4>来源文档</h4>
            <ul className="graph-doc-list">
              {selected.docs.length === 0 && <li className="empty">（无归属文档）</li>}
              {selected.docs.map((docId) => (
                <li key={docId}>{docNameById.get(docId) ?? docId}</li>
              ))}
            </ul>
          </div>
          <div className="graph-detail-sec">
            <h4>关联关系</h4>
            {related.length === 0 && <p className="empty">（无直接关联）</p>}
            <ul className="graph-rel-list">
              {related.map((r, i) => (
                <li key={i}>
                  <span className="graph-rel-text">{r.relation || '（未命名关系）'}</span>
                  <span className="graph-rel-target" onClick={() => setSelectedId(r.other)}>
                    → {r.other}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </aside>
      )}
      </div>
    </div>
  )
}