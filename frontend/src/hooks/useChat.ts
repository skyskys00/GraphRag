import { useCallback, useRef, useState } from 'react'
import type { AnswerMeta, ChatMessage, Citation, HistoryMessage } from '../types'
import { mockEventStream } from '../mocks/events'
import { subscribeStream } from '../lib/sse'

export interface UseChatOptions {
  useMock?: boolean
}

let msgSeq = 0
const nextId = () => `msg_${Date.now()}_${msgSeq++}`

async function* realEventStream(
  query: string,
  history: HistoryMessage[],
  responseType?: string,
): AsyncGenerator<import('../types').SseEvent> {
  const { events, close } = subscribeStream({
    query,
    history: JSON.stringify(history),
    response: responseType,
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
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)
  const [activeCitation, setActiveCitation] = useState<number | null>(null)
  const abortRef = useRef<{ close: () => void } | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])
  messagesRef.current = messages

  const historyFromMessages = (msgs: ChatMessage[]): HistoryMessage[] =>
    msgs
      .filter((m) => m.state === 'complete' || m.role === 'user')
      .map((m) => ({ role: m.role, content: m.text }))

  const send = useCallback(
    async (query: string, responseType?: string) => {
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
          : realEventStream(query, historyFromMessages(messagesRef.current), responseType)

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
