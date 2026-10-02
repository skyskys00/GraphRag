import { useEffect, useMemo, useState } from 'react'
import { apiUrl, compareParams } from '../lib/api'
import type { CompareResult, UploadDoc } from '../types'

interface Props {
  docs: UploadDoc[]
  collectionId: string
}

const MAX_SELECT = 6

/** 去掉扩展名并截断，用作对比列的标题 */
function shortName(name: string): string {
  const base = name.replace(/\.[a-z0-9]+$/i, '')
  return base.length > 26 ? `${base.slice(0, 25)}…` : base
}

/**
 * 器械场景：跨型号参数对比（docs/modules/DEVICE_SCENARIO.md §5.1）。
 *
 * 检索式对比——同一参数在各型号内单独检索、并排展示原文片段与页码；
 * 某型号没有该参数时后端会返回空，此处如实显示「未找到该参数」，不推断。
 */
export function DeviceCompare({ docs, collectionId }: Props) {
  // doc_id 在入库完成后才回填；未回填的（string | null）不参与对比
  const ready = useMemo(
    () => docs.filter((d): d is UploadDoc & { doc_id: string } => d.status === 'ready' && !!d.doc_id),
    [docs],
  )
  const [selected, setSelected] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<CompareResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 切库或文档列表变化时，清掉不属于当前库的选中项与旧结果
  useEffect(() => {
    setSelected((prev) => prev.filter((id) => ready.some((d) => d.doc_id === id)))
    setResult(null)
  }, [ready])

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(0, MAX_SELECT),
    )
  }

  const run = async () => {
    if (!query.trim() || selected.length === 0) return
    setLoading(true)
    setError(null)
    try {
      setResult(await compareParams({ query: query.trim(), doc_ids: selected, collection_id: collectionId }))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="cmp-view">
      <h2>参数对比</h2>
      <p className="cmp-hint">
        选 2 个以上型号，输入参数名（如「内径」「精度等级」），系统在各型号说明书内分别检索并并排展示原文；
        某型号没有该参数时如实留空，不做推断。
      </p>

      <section className="cmp-picker">
        <div className="cmp-picker-label">型号（已选 {selected.length}/{MAX_SELECT}）</div>
        {ready.length === 0 ? (
          <div className="cmp-empty">当前知识库还没有已入库的文档</div>
        ) : (
          <div className="cmp-chips">
            {ready.map((d) => (
              <button
                key={d.doc_id}
                className={`cmp-chip${selected.includes(d.doc_id) ? ' active' : ''}`}
                onClick={() => toggle(d.doc_id)}
                title={d.filename}
              >
                {shortName(d.filename)}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="cmp-query">
        <input
          value={query}
          placeholder="要对比的参数，如：内径 / 精度等级 / 电池"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void run()
          }}
        />
        <button className="cmp-run" disabled={loading || !query.trim() || selected.length === 0} onClick={() => void run()}>
          {loading ? '对比中…' : '对比'}
        </button>
      </section>

      {error && <div className="cmp-error">对比失败：{error}</div>}

      {result && (
        <section className="cmp-result">
          <div className="cmp-result-head">
            「{result.query}」 · {result.rows.length} 个型号 · 相关性门槛 {result.score_cutoff}
          </div>
          <div
            className="cmp-grid"
            style={{ gridTemplateColumns: `repeat(${Math.max(result.rows.length, 1)}, minmax(230px, 1fr))` }}
          >
            {result.rows.map((row) => (
              <div key={row.doc_id} className="cmp-col">
                <div className="cmp-col-head" title={row.doc_name}>
                  {shortName(row.doc_name)}
                </div>
                {row.error ? (
                  <div className="cmp-empty">{row.error}</div>
                ) : row.snippets.length === 0 ? (
                  <div className="cmp-empty">未找到该参数</div>
                ) : (
                  row.snippets.map((s, i) => (
                    <div key={i} className="cmp-snippet">
                      {s.image_url && (
                        <img src={apiUrl(s.image_url, collectionId)} alt="" loading="lazy" />
                      )}
                      <div className="cmp-text">{s.content}</div>
                      <div className="cmp-meta">
                        {s.page_range && s.page_range.length > 0 ? `P${s.page_range[0]} · ` : ''}
                        {s.block_type} · {s.score.toFixed(3)}
                      </div>
                    </div>
                  ))
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  )
}
