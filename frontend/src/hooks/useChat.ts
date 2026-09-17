import { useCallback, useEffect, useRef, useState } from 'react'
import type { AnswerMeta, ChatMessage, Citation, HistoryMessage } from '../types'
import { mockEventStream } from '../mocks/events'
import { subscribeStream } from '../lib/sse'

export interface UseChatOptions {
  useMock?: boolean
}

let msgSeq = 0
const nextId = () => `msg_${Date.now()}_${msgSeq++}`

const STORAGE_KEY = 'graphrag.chat.history.v1'

function isValidMessage(m: unknown): m is ChatMessage {
  if (typeof m !== 'object' || m === null) return false
  const o = m as Record<string, unknown>
  return (
    typeof o.id === 'string' &&
    (o.role === 'user' || o.role === 'assistant') &&
    typeof o.text === 'string' &&
    typeof o.state === 'string'
  )
}

// 刷新后恢复对话历史；中断的流式消息降级为 complete（保留已生成部分）
function loadHistory(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(isValidMessage)
      .map((m) =>
        m.state === 'pending' || m.state === 'streaming' ? { ...m, state: 'complete' } : m,
      )
  } catch {
    return []
  }
}

async function* realEventStream(
  query: string,
  history: HistoryMessage[],
  responseType?: string,
  collectionId?: string,
): AsyncGenerator<import('../types').SseEvent> {
  const { events, close } = subscribeStream({
    query,
    history: JSON.stringify(history),
    response: responseType,
    collectionId,
  })
  try {
    for await (const evt of events) {
      yield evt
    }
  } finally {
    close()
  }
}

export function useChat({ useMock = false }: UseChatOptions = {}) {
  const [messages, setMessages] = useState<ChatMessage[]>(loadHistory)
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)
  const [activeCitation, setActiveCitation] = useState<number | null>(null)
  const abortRef = useRef<{ close: () => void } | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])
  messagesRef.current = messages

  // 历史持久化：流式频繁更新 state，用 400ms 防抖收尾写入
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(messages))
      } catch {
        // 隐私模式 / 配额满时静默降级为不持久化
      }
    }, 400)
    return () => clearTimeout(t)
  }, [messages])

  const historyFromMessages = (msgs: ChatMessage[]): HistoryMessage[] =>
    msgs
      .filter((m) => m.state === 'complete' || m.role === 'user')
      .map((m) => ({ role: m.role, content: m.text }))

  const send = useCallback(
    async (query: string, responseType?: string, collectionId?: string) => {
      const userMsg: ChatMessage = {
        id: nextId(),
        role: 'user',
        text: query,
        state: 'complete',
      }
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: '',
        state: 'pending',
      }
      setMessages((prev) => [...prev, userMsg, assistantMsg])
      setActiveCitation(null)

      try {
        const iterator = useMock
          ? mockEventStream(query)
          : realEventStream(
              query,
              historyFromMessages(messagesRef.current),
              responseType,
              collectionId,
            )

        let text = ''
        let citations: Citation[] = []
        let meta: AnswerMeta | undefined

        setMessages((prev) =>
          prev.map((m) => (m.id === assistantMsg.id ? { ...m, state: 'streaming' } : m)),
        )

        for await (const evt of iterator) {
          if (evt.type === 'retrieved') {
            meta = evt.data
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, meta: evt.data } : m,
              ),
            )
          } else if (evt.type === 'delta') {
            text += evt.data.text
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, text } : m,
              ),
            )
          } else if (evt.type === 'citations') {
            citations = evt.data.citations
            meta = evt.data.meta
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, citations, meta: evt.data.meta }
                  : m,
              ),
            )
          } else if (evt.type === 'done') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, text: evt.data.text, state: 'complete', citations, meta }
                  : m,
              ),
            )
            break
          } else if (evt.type === 'error') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, state: 'error', error: evt.data.message }
                  : m,
              ),
            )
            break
          }
        }
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, state: 'error', error: err instanceof Error ? err.message : String(err) }
              : m,
          ),
        )
      } finally {
        abortRef.current = null
      }
    },
    [useMock],
  )

  const clear = useCallback(() => {
    abortRef.current?.close()
    abortRef.current = null
    setMessages([])
    setActiveCitation(null)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 隐私模式等：忽略
    }
  }, [])

  const currentCitations = (() => {
    const last = messages.filter((m) => m.role === 'assistant').at(-1)
    return last?.citations ?? []
  })()

  return {
    messages,
    send,
    clear,
    backendOnline,
    setBackendOnline,
    activeCitation,
    setActiveCitation,
    currentCitations,
  }
}
