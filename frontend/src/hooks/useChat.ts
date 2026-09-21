import { useCallback, useRef, useState } from 'react'
import type {
  AnswerMeta,
  ChatMessage,
  Citation,
  ConversationDetail,
  ConversationInfo,
} from '../types'
import {
  createConversation,
  deleteConversation as apiDeleteConversation,
  fetchConversation,
  listConversations,
  renameConversation as apiRenameConversation,
} from '../lib/api'
import { mockEventStream } from '../mocks/events'
import { subscribeStream } from '../lib/sse'

export interface UseChatOptions {
  useMock?: boolean
}

let msgSeq = 0
const nextId = () => `msg_${Date.now()}_${msgSeq++}`

// 后端会话明细（完整 messages，含 citations/meta）转前端渲染态
function conversationToMessages(conv: ConversationDetail): ChatMessage[] {
  return conv.messages
    .filter((m) => m.content)
    .map((m, i) => ({
      id: `hist_${conv.conversation_id}_${i}`,
      role: m.role,
      text: m.content,
      state: 'complete' as const,
      citations: m.citations ?? undefined,
      meta: m.meta ?? undefined,
    }))
}

async function* realEventStream(
  query: string,
  responseType: string | undefined,
  collectionId: string,
  conversationId?: string,
): AsyncGenerator<import('../types').SseEvent> {
  const { events, close } = subscribeStream({
    query,
    response: responseType,
    collectionId,
    conversationId,
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
  const [conversations, setConversations] = useState<ConversationInfo[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)
  // 各会话的生成中状态（key = conversation_id），切走后仍能看到哪个会话在跑
  const [streamingMap, setStreamingMap] = useState<Record<string, boolean>>({})
  const streamingMapRef = useRef<Record<string, boolean>>({})
  const [activeCitation, setActiveCitation] = useState<number | null>(null)
  const abortRef = useRef<Record<string, { close: () => void }>>({})
  const currentIdRef = useRef<string | null>(null)
  currentIdRef.current = currentId

  const setStreaming = (convId: string | null, val: boolean) => {
    if (!convId) return
    streamingMapRef.current = { ...streamingMapRef.current, [convId]: val }
    setStreamingMap((prev) => ({ ...prev, [convId]: val }))
  }
  const isStreaming = (convId: string | null) =>
    convId ? !!streamingMapRef.current[convId] : false

  const loadConversations = useCallback(
    async (collectionId = 'default'): Promise<ConversationInfo[]> => {
      if (useMock) {
        setConversations([])
        return []
      }
      try {
        const list = await listConversations(collectionId)
        setConversations(list)
        return list
      } catch {
        return []
      }
    },
    [useMock],
  )

  const selectConversation = useCallback(
    async (id: string | null, collectionId = 'default') => {
      if (useMock || !id) {
        setCurrentId(id)
        setMessages([])
        return
      }
      try {
        const conv = await fetchConversation(id, collectionId)
        setCurrentId(id)
        setMessages(conversationToMessages(conv))
      } catch {
        // 后端离线或会话已删：保持现状
      }
    },
    [useMock],
  )

  const newConversation = useCallback(
    async (collectionId = 'default') => {
      if (useMock) {
        setCurrentId(null)
        setMessages([])
        return null
      }
      const { conversation_id } = await createConversation(collectionId)
      setCurrentId(conversation_id)
      setMessages([])
      void loadConversations(collectionId)
      return conversation_id
    },
    [useMock, loadConversations],
  )

  const renameConversation = useCallback(
    async (id: string, title: string, collectionId = 'default') => {
      await apiRenameConversation(id, title, collectionId)
      setConversations((prev) =>
        prev.map((c) => (c.conversation_id === id ? { ...c, title } : c)),
      )
    },
    [],
  )

  const deleteConversation = useCallback(
    async (id: string, collectionId = 'default') => {
      // 正在生成的会话：先关连接再删（后端仍会落库，但前端不等了）
      const abort = abortRef.current[id]
      if (abort) {
        abort.close()
        delete abortRef.current[id]
      }
      setStreaming(id, false)
      await apiDeleteConversation(id, collectionId)
      setConversations((prev) => prev.filter((c) => c.conversation_id !== id))
      if (currentIdRef.current === id) {
        setCurrentId(null)
        setMessages([])
        setActiveCitation(null)
      }
    },
    [],
  )

  const send = useCallback(
    async (
      query: string,
      responseType?: string,
      collectionId?: string,
      conversationId?: string,
    ) => {
      const col = collectionId ?? 'default'
      setActiveCitation(null)

      let convId = conversationId ?? currentIdRef.current
      // 无会话时懒创建（多轮历史由后端从会话读取）
      if (!convId && !useMock) {
        const { conversation_id } = await createConversation(col)
        convId = conversation_id
        setCurrentId(convId)
        void loadConversations(col)
      }

      // 标记该会话生成中
      setStreaming(convId, true)

      const userMsg: ChatMessage = { id: nextId(), role: 'user', text: query, state: 'complete' }
      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: '',
        state: 'pending',
      }

      // 如果是当前会话，直接追加到 messages；否则不更新当前视图
      if (currentIdRef.current === convId) {
        setMessages((prev) => [...prev, userMsg, assistantMsg])
      }

      // 流式期间切走会话则停止渲染（后端仍会落库，切回可见）
      const patchIfActive = (fn: (prev: ChatMessage[]) => ChatMessage[]) => {
        if (currentIdRef.current === convId) setMessages(fn)
      }

      try {
        const iterator = useMock
          ? mockEventStream(query)
          : realEventStream(query, responseType, col, convId ?? undefined)

        // 存 abort 句柄（按会话）
        // realEventStream 是 generator，没法直接 abort；先记录流式状态
        let text = ''
        let citations: Citation[] = []
        let meta: AnswerMeta | undefined

        patchIfActive((prev) =>
          prev.map((m) => (m.id === assistantMsg.id ? { ...m, state: 'streaming' } : m)),
        )

        for await (const evt of iterator) {
          if (evt.type === 'retrieved') {
            meta = evt.data
            patchIfActive((prev) =>
              prev.map((m) => (m.id === assistantMsg.id ? { ...m, meta: evt.data } : m)),
            )
          } else if (evt.type === 'delta') {
            text += evt.data.text
            patchIfActive((prev) =>
              prev.map((m) => (m.id === assistantMsg.id ? { ...m, text } : m)),
            )
          } else if (evt.type === 'citations') {
            citations = evt.data.citations
            meta = evt.data.meta
            patchIfActive((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, citations, meta: evt.data.meta } : m,
              ),
            )
          } else if (evt.type === 'done') {
            patchIfActive((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, text: evt.data.text, state: 'complete', citations, meta }
                  : m,
              ),
            )
            break
          } else if (evt.type === 'error') {
            patchIfActive((prev) =>
              prev.map((m) =>
                m.id === assistantMsg.id ? { ...m, state: 'error', error: evt.data.message } : m,
              ),
            )
            break
          }
        }
      } catch (err) {
        patchIfActive((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, state: 'error', error: err instanceof Error ? err.message : String(err) }
              : m,
          ),
        )
      } finally {
        if (convId) {
          setStreaming(convId, false)
          if (abortRef.current[convId]) {
            delete abortRef.current[convId]
          }
        }
        if (!useMock) void loadConversations(col)
      }
    },
    [useMock, loadConversations],
  )

  const currentCitations = (() => {
    const last = messages.filter((m) => m.role === 'assistant').at(-1)
    return last?.citations ?? []
  })()

  return {
    messages,
    conversations,
    currentConversationId: currentId,
    streamingMap,
    isStreaming,
    send,
    loadConversations,
    selectConversation,
    newConversation,
    renameConversation,
    deleteConversation,
    backendOnline,
    setBackendOnline,
    activeCitation,
    setActiveCitation,
    currentCitations,
  }
}