import type { SseEvent } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

/**
 * 订阅 SSE 流式回答。返回 AsyncIterable<SseEvent>。
 * done/error 事件驱动结束；底层连接意外断开时兜底发 error。
 */
export function subscribeStream(params: {
  query: string
  history?: string // JSON 字符串
  response?: string
  collectionId?: string
  conversationId?: string
}): {
  events: AsyncIterable<SseEvent>
  close: () => void
} {
  const url = new URL(`${API_BASE}/answer/stream`, window.location.origin)
  url.searchParams.set('q', params.query)
  if (params.history) url.searchParams.set('history', params.history)
  if (params.response) url.searchParams.set('response', params.response)
  if (params.collectionId) url.searchParams.set('collection_id', params.collectionId)
  if (params.conversationId) url.searchParams.set('conversation_id', params.conversationId)

  const es = new EventSource(url.toString())

  const stream = new ReadableStream<SseEvent>({
    start(controller) {
      let finished = false
      const enqueue = (evt: SseEvent) => {
        if (finished) return
        controller.enqueue(evt)
      }
      const finish = () => {
        if (finished) return
        finished = true
        controller.close()
        es.close()
      }

      const parseMsg = (e: Event) => (e as MessageEvent).data

      es.addEventListener('retrieved', (e) =>
        enqueue({ type: 'retrieved', data: JSON.parse(parseMsg(e)) }),
      )
      es.addEventListener('delta', (e) =>
        enqueue({ type: 'delta', data: JSON.parse(parseMsg(e)) }),
      )
      es.addEventListener('citations', (e) =>
        enqueue({ type: 'citations', data: JSON.parse(parseMsg(e)) }),
      )
      es.addEventListener('done', (e) => {
        enqueue({ type: 'done', data: JSON.parse(parseMsg(e)) })
        finish()
      })
      // 同时承接：后端 error 事件（有 data）与连接异常（无 data）
      es.addEventListener('error', (e) => {
        const d = parseMsg(e)
        enqueue({
          type: 'error',
          data: d ? (JSON.parse(d) as { message: string }) : { message: '连接已断开,请稍后重试' },
        })
        finish()
      })
    },
    cancel() {
      es.close()
    },
  })

  const iterator = stream.getReader()
  const events: AsyncIterable<SseEvent> = {
    [Symbol.asyncIterator](): AsyncIterator<SseEvent> {
      return {
        async next() {
          const r = await iterator.read()
          return { value: r.value!, done: r.done }
        },
        async return() {
          iterator.releaseLock()
          es.close()
          return { done: true, value: undefined }
        },
      }
    },
  }

  return { events, close: () => es.close() }
}