import { splitCitations } from '../lib/cite'
import type { ChatMessage } from '../types'

interface MessageBubbleProps {
  message: ChatMessage
  onCitationClick: (marker: number) => void
  activeCitation: number | null
}

export function MessageBubble({ message, onCitationClick, activeCitation }: MessageBubbleProps) {
  const isUser = message.role === 'user'

  if (message.state === 'error') {
    return (
      <div className="msg assistant">
        <div className="avatar">AI</div>
        <div className="bubble error">
          <div className="error-title">生成失败</div>
          <div className="error-desc">{message.error ?? '未知错误'}</div>
          {message.text && (
            <details>
              <summary>已生成内容（可复制）</summary>
              <div className="error-text">{message.text}</div>
            </details>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={`msg ${isUser ? 'user' : 'assistant'}`}>
      <div className="avatar">{isUser ? '我' : 'AI'}</div>
      <div className="bubble">
        {isUser ? (
          <span>{message.text}</span>
        ) : (
          <RenderedText
            text={message.text}
            hasCitations={!!message.citations?.length}
            onCitationClick={onCitationClick}
            activeCitation={activeCitation}
          />
        )}
        {!isUser && message.state === 'streaming' && (
          <span className="cursor" aria-hidden>
            ▍
          </span>
        )}
      </div>
    </div>
  )
}

function RenderedText({
  text,
  hasCitations,
  onCitationClick,
  activeCitation,
}: {
  text: string
  hasCitations: boolean
  onCitationClick: (marker: number) => void
  activeCitation: number | null
}) {
  const parts = hasCitations ? splitCitations(text) : [{ kind: 'text' as const, content: text }]
  return (
    <>
      {parts.map((p, i) =>
        p.kind === 'text' ? (
          <span key={i}>{p.content}</span>
        ) : (
          <button
            key={i}
            className={`citation ${activeCitation === p.marker ? 'active' : ''}`}
            onClick={() => onCitationClick(p.marker)}
            title={`跳转至引用 ${p.marker}`}
          >
            [{p.marker}]
          </button>
        ),
      )}
    </>
  )
}
