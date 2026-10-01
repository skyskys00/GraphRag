import type { Citation, SseEvent } from '../types'

/**
 * 离线 mock：模拟一整条 SSE 事件流，供开发冒烟、无后端时走查。
 * 真流式（逐字 delta），citations / done 事件齐全。
 */
export const mockCitations: Citation[] = [
  {
    marker: 1,
    text_unit_id: 'complaint-sop-chunk-001',
    full_doc_id: 'complaint_sop',
    file_path: '客户服务投诉处理SOP.pdf',
    title_path: null,
    page_range: [3, 4],
    anchor: null,
    snippet: '投诉分级处理机制：一级（一线 24h 直接回复）、二级（主管 72h 首次回复）、三级（专项小组 1h 响应并上报）…',
    score: 0.92,
    image_path: null,
  },
  {
    marker: 2,
    text_unit_id: 'complaint-sop-chunk-003',
    full_doc_id: 'complaint_sop',
    file_path: '客户服务投诉处理SOP.pdf',
    title_path: null,
    page_range: [5, 6],
    anchor: null,
    snippet: '回访与考核：闭环后 3 工作日内质检组抽访 30%，满意度纳入月度考核指标…',
    score: 0.86,
    image_path: null,
  },
  {
    marker: 3,
    text_unit_id: 'meeting-2025q2-chunk-002',
    full_doc_id: 'meeting_q2',
    file_path: '智能客服系统迭代排期会议纪要.md',
    title_path: '二季度迭代 / 投诉工单升级',
    page_range: null,
    anchor: null,
    snippet: 'Q3 重点：客服工单系统支持三级流转，升级投诉自动派单至主管…',
    score: 0.71,
    image_path: null,
  },
]

const FULL_TEXT = `客户投诉实行分级处理机制，整体分为受理、分级、处置、回访四个阶段。[1]

一级投诉（一般投诉）由一线客服在 24 小时内直接受理并回复，工单闭环率需达 95% 以上。[2] 二级投诉（升级投诉）由客服主管牵头，联合业务部门给出方案，72 小时内完成首次回复。[3] 三级投诉（重大投诉）由客服项目经理启动专项小组，1 小时内响应并上报管理层。[1]

回访环节：所有投诉在闭环后 3 个工作日内由质检组随机抽访 30%，满意度纳入月度考核。[2]`

export async function* mockEventStream(query: string): AsyncGenerator<SseEvent> {
  yield {
    type: 'retrieved',
    data: {
      mode: 'single-window',
      used_chunks: 4,
      context_tokens: 1280,
      source_docs: ['客户服务投诉处理SOP.pdf', '智能客服系统迭代排期会议纪要.md'],
    },
  }

  // 模拟 token 级流式：按 ~2-3 字为一步，间隔 25-40ms
  let i = 0
  while (i < FULL_TEXT.length) {
    const step = Math.max(1, Math.floor(Math.random() * 3) + 1)
    const chunk = FULL_TEXT.slice(i, i + step)
    i += step
    yield { type: 'delta', data: { text: chunk } }
    await new Promise((r) => setTimeout(r, 25 + Math.random() * 20))
  }

  yield {
    type: 'citations',
    data: {
      citations: mockCitations,
      meta: {
        mode: 'single-window',
        used_chunks: 4,
        unmatched_markers: 0,
        source_docs: ['客户服务投诉处理SOP.pdf', '智能客服系统迭代排期会议纪要.md'],
      },
    },
  }

  yield { type: 'done', data: { text: FULL_TEXT, query } }
}
