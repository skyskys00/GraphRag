import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Graph } from '@antv/g6'
import type { Element as GElement } from '@antv/g'
import type {
  EdgeData as G6EdgeData,
  GraphOptions,
  IPointerEvent,
  NodeData as G6NodeData,
} from '@antv/g6'
import { fetchGraph } from '../lib/api'
import type {
  DocGraphEdge,
  DocumentGraphData,
  EntityGraphData,
  GraphData,
  UploadDoc,
} from '../types'

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
type GraphLevel = 'document' | 'entity'

interface EntityNodeInfo {
  entityType: string
  description: string
  docs: string[]
  chunks: string[]
}

interface DocNodeInfo {
  label: string
  entityCount: number
  clusterId: number
  createdAt: string
}

// 实体类型色板（保持原有）
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
const DEFAULT_ENTITY_FILL = '#b3bac4'

// Phase 3：实体类型过滤 chip（7 大类，顺序与后端 normalize_entity_type 一致）+ 核心节点数
const ENTITY_GROUPS = ['组织', '人物', '产品/项目', '概念', '事件', '地点', '其他'] as const
const TOP_N_DEFAULT = 60

// 实体类型 chip 色板（与 TYPE_COLORS 呼应）
const ENTITY_GROUP_COLORS: Record<string, string> = {
  组织: '#92c47c',
  人物: '#e8a05b',
  '产品/项目': '#e2825a',
  概念: '#f4b943',
  事件: '#f29b9b',
  地点: '#b8c25e',
  其他: '#aab0b8',
}

// 实体级关系类型色板（6 类 + 未分类，与后端 classify_relation 对应）
const RELATION_TYPE_COLORS: Record<number, string> = {
  1: '#5b8def', // 归属/组成 - 蓝
  2: '#6aa96a', // 动作/执行 - 绿
  3: '#c46453', // 因果/影响 - 红
  4: '#9a7bc4', // 时间/先后 - 紫
  5: '#6a9ea3', // 同义/相关 - 青灰
  6: '#d4915c', // 属性/数值 - 橙
  0: '#a89b8d', // 未分类 - 灰
}
const RELATION_TYPE_NAMES: Record<number, string> = {
  1: '归属/组成',
  2: '动作/执行',
  3: '因果/影响',
  4: '时间/先后',
  5: '同义/相关',
  6: '属性/数值',
  0: '未分类',
}

function trunc(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n)}…` : s
}

function isDocumentData(d: GraphData): d is DocumentGraphData {
  return d.level === 'document'
}

function isEntityData(d: GraphData): d is EntityGraphData {
  return d.level === 'entity'
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
  const [graphLevel, setGraphLevel] = useState<GraphLevel>('document')
  const [hiddenRelTypes, setHiddenRelTypes] = useState<Set<number>>(new Set())
  // Phase 3：默认仅核心节点（top_n=60），可切全部；类型过滤为前端视图过滤
  const [coreOnly, setCoreOnly] = useState(true)
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set())

  const rawDataRef = useRef<GraphData | null>(null)
  const entityInfoRef = useRef<Map<string, EntityNodeInfo>>(new Map())
  const docInfoRef = useRef<Map<string, DocNodeInfo>>(new Map())
  const graphRenderPRef = useRef<Promise<void> | null>(null)

  // 数据版本：level/filterDoc 变化 +1，驱动图重建
  const [dataVersion, setDataVersion] = useState(0)

  const docNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const d of uploadDocs) if (d.doc_id) m.set(d.doc_id, d.filename)
    return m
  }, [uploadDocs])

  // 加载数据
  useEffect(() => {
    let alive = true
    setErrorMsg('')
    setSelectedId(null)
    setFocusInfo(null)
    async function load() {
      try {
        const raw = await fetchGraph({
          level: graphLevel,
          docId: graphLevel === 'entity' ? filterDoc || undefined : undefined,
          collection_id: collectionId,
          top_n: graphLevel === 'entity' && coreOnly ? TOP_N_DEFAULT : 0,
        })
        if (!alive) return
        rawDataRef.current = raw

        if (isEntityData(raw)) {
          entityInfoRef.current = new Map(raw.nodes.map((n) => [n.id, {
            entityType: n.entity_type,
            description: n.description,
            docs: n.docs,
            chunks: n.chunks,
          }]))
        } else if (isDocumentData(raw)) {
          docInfoRef.current = new Map(raw.nodes.map((n) => [n.id, {
            label: n.label,
            entityCount: n.entity_count,
            clusterId: n.cluster_id,
            createdAt: n.created_at,
          }]))
        }

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
  }, [graphLevel, filterDoc, attempt, collectionId, coreOnly])

  // 渲染图谱
  useEffect(() => {
    if (loadState !== 'ready') return
    const el = containerRef.current
    const raw = rawDataRef.current
    if (!el || !raw) return

    let g6Nodes: G6NodeData[]
    let g6Edges: G6EdgeData[]
    let layoutLinkDistance = 160

    if (isDocumentData(raw)) {
      // ---------- 文档级 ----------
      const clusterColor = new Map<number, string>()
      for (const c of raw.clusters) clusterColor.set(c.id, c.color)

      // 节点大小：按 entity_count 映射到 36-72
      const counts = raw.nodes.map((n) => n.entity_count)
      const minC = Math.min(...counts, 1)
      const maxC = Math.max(...counts, 1)
      const sizeOf = (c: number) => {
        if (maxC === minC) return 48
        return 36 + ((c - minC) / (maxC - minC)) * 36
      }

      g6Nodes = raw.nodes.map((n) => ({
        id: n.id,
        style: {
          size: sizeOf(n.entity_count),
          fill: clusterColor.get(n.cluster_id) ?? '#a89b8d',
          stroke: '#ffffff',
          lineWidth: 2,
          radius: 8,
          labelText: trunc(n.label, 16),
          labelPlacement: 'bottom',
          labelFontSize: 11,
          labelFill: '#5c5448',
          labelMaxWidth: 100,
        },
        data: {
          level: 'document',
          clusterId: n.cluster_id,
          entityCount: n.entity_count,
          label: n.label,
        },
      }))

      // 边：概念关联粗细按 weight 映射 1-5px
      g6Edges = raw.edges.map((e: DocGraphEdge, i) => {
        const w = e.weight ?? 0
        const lineWidth = 1 + Math.min(w * 12, 4)
        return {
          id: `e${i}`,
          source: e.source,
          target: e.target,
          style: {
            stroke: e.type === 'citation' ? '#8b5a2e' : '#c8b9a8',
            lineWidth,
            endArrow: e.type === 'citation' ? true : false,
          },
          data: { type: e.type, weight: w, shared_entities: e.shared_entities },
        }
      })
      layoutLinkDistance = 200
    } else {
      // ---------- 实体级（保持原有）----------
      // 类型过滤：视图层过滤节点，并把悬空边的端点一并排除
      const visibleNodes = raw.nodes.filter((n) => !hiddenGroups.has(n.entity_group))
      const visibleIds = new Set(visibleNodes.map((n) => n.id))
      g6Nodes = visibleNodes.map((n) => ({
        id: n.id,
        style: {
          size: 30,
          fill: ENTITY_GROUP_COLORS[n.entity_group] ?? TYPE_COLORS[n.entity_type] ?? DEFAULT_ENTITY_FILL,
          stroke: '#ffffff',
          lineWidth: 2,
          labelText: trunc(n.id, 14),
          labelPlacement: 'bottom',
          labelFontSize: 11,
          labelFill: '#6b7480',
        },
        data: {
          level: 'entity',
          entityType: n.entity_type,
          description: n.description,
          docs: n.docs,
          chunks: n.chunks,
        },
      }))

      g6Edges = raw.edges
        .filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
        .filter((e) => !hiddenRelTypes.has(e.rel_type))
        .map((e, i) => ({
          id: `e${i}`,
          source: e.source,
          target: e.target,
          style: {
            stroke: RELATION_TYPE_COLORS[e.rel_type] ?? '#ccd3dc',
            lineWidth: Math.min(1 + (e.weight > 0 ? Math.log2(1 + e.weight) : 0), 3.5),
            labelText: e.relation && e.weight >= 2 ? trunc(e.relation, 10) : undefined,
            labelPlacement: 'center',
            labelFontSize: 10,
            labelFill: '#99a2b0',
          },
          data: {
            relType: e.rel_type,
            relTypeName: e.rel_type_name,
            description: e.relation,
          },
        }))
    }

    const options: GraphOptions = {
      container: el,
      data: { nodes: g6Nodes, edges: g6Edges },
      autoFit: { type: 'view' },
      layout: {
        type: 'force',
        preventOverlap: true,
        linkDistance: layoutLinkDistance,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadState, dataVersion, hiddenRelTypes, hiddenGroups])

  // 引用聚焦（仅实体级）
  const applyFocus = useCallback(async (graph: Graph, f: GraphFocus) => {
    const info = entityInfoRef.current
    const raw = rawDataRef.current
    if (!raw || !isEntityData(raw) || info.size === 0) return

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
    // 自动切到实体级（引用聚焦只能在实体级生效）
    if (graphLevel !== 'entity') {
      setGraphLevel('entity')
      return
    }
    const graph = graphReady ? graphRef.current : null
    if (graph) void applyFocus(graph, focus)
  }, [focus, graphReady, applyFocus, graphLevel])

  // 点击节点 → 选中 + 详情
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

  // 双击文档节点 → 下钻到实体级
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    const onDblClick = (evt: IPointerEvent) => {
      const raw = rawDataRef.current
      if (!raw || !isDocumentData(raw)) return
      const id = String((evt.target as GElement).id)
      // 切到实体级 + 过滤该文档
      setFilterDoc(id)
      setGraphLevel('entity')
    }
    graph.on('node:dblclick', onDblClick)
    return () => {
      graph.off('node:dblclick', onDblClick)
    }
  }, [dataVersion])

  // 选中高亮
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

  const raw = rawDataRef.current
  const isDocLevel = raw && isDocumentData(raw)
  const isEntLevel = raw && isEntityData(raw)

  // 详情卡数据
  const selectedEntity = selectedId && isEntLevel ? entityInfoRef.current.get(selectedId) : undefined
  const selectedDoc = selectedId && isDocLevel ? docInfoRef.current.get(selectedId) : undefined

  const relatedEdges: {
    label: string
    other: string
    weight: number
    sharedEntities?: string[]
    type?: string
    relType?: number
    relTypeName?: string
  }[] = useMemo(() => {
    if (!selectedId || !raw) return []
    if (isEntityData(raw)) {
      return raw.edges
        .filter((e) => e.source === selectedId || e.target === selectedId)
        .map((e) => ({
          label: e.relation || '（未命名关系）',
          other: e.source === selectedId ? e.target : e.source,
          weight: e.weight,
          relType: e.rel_type,
          relTypeName: e.rel_type_name,
        }))
    }
    if (isDocumentData(raw)) {
      return raw.edges
        .filter((e) => e.source === selectedId || e.target === selectedId)
        .map((e) => ({
          label: e.type === 'citation' ? '引用' : `概念关联 (Jaccard: ${e.weight.toFixed(2)})`,
          other: e.source === selectedId ? e.target : e.source,
          weight: e.weight,
          sharedEntities: e.shared_entities,
          type: e.type,
        }))
    }
    return []
  }, [selectedId, raw])

  const clusterById = useMemo(() => {
    if (!isDocLevel) return new Map<number, string>()
    return new Map((raw as DocumentGraphData).clusters.map((c) => [c.id, c.name]))
  }, [raw, isDocLevel])

  // 实体级：关系类型统计（按数量降序）
  const entityRelTypeStats = useMemo(() => {
    if (!isEntLevel) return [] as { typeId: number; count: number }[]
    const counts = new Map<number, number>()
    for (const e of (raw as EntityGraphData).edges) {
      counts.set(e.rel_type, (counts.get(e.rel_type) ?? 0) + 1)
    }
    return [...counts.entries()]
      .map(([typeId, count]) => ({ typeId, count }))
      .sort((a, b) => b.count - a.count)
  }, [raw, isEntLevel])

  const toggleRelType = (typeId: number) => {
    setHiddenRelTypes((prev) => {
      const next = new Set(prev)
      if (next.has(typeId)) next.delete(typeId)
      else next.add(typeId)
      return next
    })
  }

  // 实体级：7 大类类型计数（供类型过滤 chip 显示）
  const entityGroupStats = useMemo(() => {
    if (!isEntLevel) return [] as { group: string; count: number }[]
    const counts = new Map<string, number>()
    for (const n of (raw as EntityGraphData).nodes) {
      counts.set(n.entity_group, (counts.get(n.entity_group) ?? 0) + 1)
    }
    return ENTITY_GROUPS.map((g) => ({ group: g, count: counts.get(g) ?? 0 }))
  }, [raw, isEntLevel])

  const toggleGroup = (group: string) => {
    setHiddenGroups((prev) => {
      const next = new Set(prev)
      if (next.has(group)) next.delete(group)
      else next.add(group)
      return next
    })
  }

  const handleLevelChange = (level: GraphLevel) => {
    if (level === graphLevel) return
    // 切回文档级时清掉文档过滤
    if (level === 'document') {
      setFilterDoc('')
      setHiddenRelTypes(new Set())
    }
    setGraphLevel(level)
  }

  return (
    <div className="graph-view">
      <div className="graph-toolbar">
        <div className="graph-level-switch" role="tablist" aria-label="图谱粒度">
          <button
            className={`graph-level-btn${graphLevel === 'document' ? ' active' : ''}`}
            onClick={() => handleLevelChange('document')}
            role="tab"
            aria-selected={graphLevel === 'document'}
          >
            文档视图
          </button>
          <button
            className={`graph-level-btn${graphLevel === 'entity' ? ' active' : ''}`}
            onClick={() => handleLevelChange('entity')}
            role="tab"
            aria-selected={graphLevel === 'entity'}
          >
            实体视图
          </button>
        </div>

        {isEntLevel && (
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
        )}

        {isEntLevel && (
          <div className="graph-core-switch" role="group" aria-label="实体筛选范围">
            <button
              type="button"
              className={`graph-core-btn${coreOnly ? ' active' : ''}`}
              onClick={() => setCoreOnly(true)}
              title={filterDoc ? '单文档视图默认全量展示' : '按 PageRank 只保留核心实体'}
            >
              核心实体 ({TOP_N_DEFAULT})
            </button>
            <button
              type="button"
              className={`graph-core-btn${!coreOnly ? ' active' : ''}`}
              onClick={() => setCoreOnly(false)}
            >
              全部实体
            </button>
          </div>
        )}

        {isDocLevel && (
          <button
            className="graph-back-btn"
            onClick={() => handleLevelChange('entity')}
            style={{ display: filterDoc ? 'inline-flex' : 'none' }}
          >
            ← 返回文档视图
          </button>
        )}

        {loadState === 'ready' && raw && (
          <span className="graph-count">
            {isDocLevel
              ? `${raw.meta.node_count} 篇文档 · ${raw.meta.edge_count} 条关联 · ${raw.meta.cluster_count} 个话题`
              : `${filterDoc ? '该文档关联 ' : ''}${raw.meta.node_count} 节点 · ${raw.meta.edge_count} 边`}
          </span>
        )}
      </div>

      {isEntLevel && entityGroupStats.length > 0 && (
        <div className="graph-type-chips" role="group" aria-label="实体类型过滤">
          <span className="graph-chips-label">实体类型</span>
          {entityGroupStats.map(({ group, count }) => {
            const off = hiddenGroups.has(group)
            return (
              <button
                key={group}
                type="button"
                className={`graph-chip${off ? ' off' : ''}`}
                onClick={() => toggleGroup(group)}
                title={`点击${off ? '显示' : '隐藏'}「${group}」实体（${count} 个）`}
                style={{ '--chip-color': ENTITY_GROUP_COLORS[group] ?? '#aaa' } as CSSProperties}
              >
                <span className="graph-chip-dot" aria-hidden />
                {group} ({count})
              </button>
            )
          })}
        </div>
      )}

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
          {isDocLevel && (
            <div className="graph-legend">
              <div className="graph-legend-title">话题聚类</div>
              {(raw as DocumentGraphData).clusters.map((c) => (
                <div key={c.id} className="graph-legend-item" title={`${c.doc_count} 篇文档`}>
                  <span
                    className="graph-legend-dot"
                    style={{ backgroundColor: c.color }}
                    aria-hidden
                  />
                  <span className="graph-legend-text">
                    {trunc(c.name, 12)} <span className="graph-legend-count">({c.doc_count})</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          {isEntLevel && (
            <div className="graph-legend">
              <div className="graph-legend-title">关系类型</div>
              {entityRelTypeStats.map(({ typeId, count }) => {
                const hidden = hiddenRelTypes.has(typeId)
                return (
                  <div
                    key={typeId}
                    className={`graph-legend-item${hidden ? ' muted' : ''}`}
                    title={`点击${hidden ? '显示' : '隐藏'}该类关系`}
                    onClick={() => toggleRelType(typeId)}
                    style={{ cursor: 'pointer' }}
                  >
                    <span
                      className="graph-legend-dot"
                      style={{
                        backgroundColor: RELATION_TYPE_COLORS[typeId] ?? '#ccc',
                        borderRadius: 0,
                        height: 3,
                        alignSelf: 'center',
                      }}
                      aria-hidden
                    />
                    <span className="graph-legend-text">
                      {RELATION_TYPE_NAMES[typeId] ?? `类型${typeId}`}{' '}
                      <span className="graph-legend-count">({count})</span>
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* ---------- 详情卡 ---------- */}
        {(selectedEntity || selectedDoc) && (
          <aside className="graph-detail">
            <div className="graph-detail-head">
              <span className="graph-type-tag">
                {selectedDoc ? '文档' : selectedEntity?.entityType}
              </span>
              <button className="graph-close" onClick={() => setSelectedId(null)} aria-label="关闭详情">
                ×
              </button>
            </div>

            {selectedDoc && (
              <>
                <h3 className="graph-detail-title" title={selectedDoc.label}>
                  {selectedDoc.label}
                </h3>
                <div className="graph-detail-meta">
                  <div><span className="graph-meta-key">实体数</span><span className="graph-meta-val">{selectedDoc.entityCount}</span></div>
                  <div>
                    <span className="graph-meta-key">话题</span>
                    <span className="graph-meta-val">{clusterById.get(selectedDoc.clusterId) ?? `话题 ${selectedDoc.clusterId}`}</span>
                  </div>
                </div>
                <div className="graph-detail-sec">
                  <h4>关联文档（{relatedEdges.length}）</h4>
                  {relatedEdges.length === 0 && <p className="empty">（无关联）</p>}
                  <ul className="graph-rel-list">
                    {relatedEdges.map((r, i) => (
                      <li key={i}>
                        <span className="graph-rel-text">{r.label}</span>
                        <span className="graph-rel-target" onClick={() => setSelectedId(r.other)}>
                          → {docNameById.get(r.other) ?? r.other}
                        </span>
                        {r.sharedEntities && r.sharedEntities.length > 0 && (
                          <div className="graph-rel-shared">
                            共享实体：{r.sharedEntities.join('、')}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
                <button
                  className="graph-drill-btn"
                  onClick={() => {
                    if (selectedId) {
                      setFilterDoc(selectedId)
                      setGraphLevel('entity')
                    }
                  }}
                >
                  查看该文档实体图谱 →
                </button>
              </>
            )}

            {selectedEntity && (
              <>
                <h3 className="graph-detail-title">{selectedId}</h3>
                {selectedEntity.description ? (
                  <p className="graph-detail-desc">{selectedEntity.description}</p>
                ) : (
                  <p className="graph-detail-desc empty">（该实体无描述）</p>
                )}
                <div className="graph-detail-sec">
                  <h4>来源文档</h4>
                  <ul className="graph-doc-list">
                    {selectedEntity.docs.length === 0 && <li className="empty">（无归属文档）</li>}
                    {selectedEntity.docs.map((docId) => (
                      <li key={docId}>{docNameById.get(docId) ?? docId}</li>
                    ))}
                  </ul>
                </div>
                <div className="graph-detail-sec">
                  <h4>关联关系</h4>
                  {relatedEdges.length === 0 && <p className="empty">（无直接关联）</p>}
                  <ul className="graph-rel-list">
                    {relatedEdges.map((r, i) => (
                      <li key={i}>
                        {r.relTypeName && (
                          <span
                            className="graph-rel-type-tag"
                            style={{
                              backgroundColor: RELATION_TYPE_COLORS[r.relType ?? 0] ?? '#ccc',
                            }}
                          >
                            {r.relTypeName}
                          </span>
                        )}
                        <span className="graph-rel-text">{r.label}</span>
                        <span className="graph-rel-target" onClick={() => setSelectedId(r.other)}>
                          → {r.other}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
