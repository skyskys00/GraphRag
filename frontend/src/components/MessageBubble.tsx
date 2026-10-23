import { splitCitations } from '../lib/cite'
import type { ChatMessage, ToolStep } from '../types'

// M10 工具可读名（未知名回退原名）
const TOOL_LABEL: Record<string, string> = {
  tool_retrieve: '知识库检索',
  tool_rag_answer: '综合应答',
  tool_list_docs: '文档清单',
}

function stepQuery(step: ToolStep): string | undefined {
  if (!step.args) return undefined
  try {
    const o: unknown = JSON.parse(step.args)
    const q = (o as { query?: unknown })?.query
    return typeof q === 'string' && q.trim() ? q : undefined
  } catch {
    return undefined
  }
}

function roundTitle(round: ToolStep[]): string {
  const decided = round.filter((s) => s.summary === undefined)
  const results = round.filter((s) => s.summary !== undefined)
  const label = decided.map((s) => TOOL_LABEL[s.tool] ?? s.tool).join('、')
  const hints = [...new Set(results.map((s) => s.summary).filter(Boolean))].join(' · ')
  return hints ? `${label}（${hints}）` : label
}

function ToolRounds({ message }: { message: ChatMessage }) {
  const rounds = message.rounds
  if (!rounds || rounds.length === 0) return null
  return (
    <div className="steps">
      {rounds.map((round, i) => {
        const isLast = i === rounds.length - 1
        const live = isLast && (message.state === 'pending' || message.state === 'streaming')
        return (
          <details key={i} className="step-card" open={live}>
            <summary>{roundTitle(round)}</summary>
            <div className="step-body">
              {round.map((s, j) => {
                const q = stepQuery(s)
                const label = TOOL_LABEL[s.tool] ?? s.tool
                return (
                  <div key={j} className="step-row">
                    <span className="step-label">{label}</span>
                    {q && <span className="step-args">“{q}”</span>}
                    {s.summary && <span className="step-summary">{s.summary}</span>}
                  </div>
                )
              })}
            </div>
          </details>
        )
      })}
    </div>
  )
}

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
          <>
            <ToolRounds message={message} />
            <RenderedText
              text={message.text}
              hasCitations={!!message.citations?.length}
              onCitationClick={onCitationClick}
              activeCitation={activeCitation}
            />
          </>
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
