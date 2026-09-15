import type { ChatMessage } from '../types'
import { MessageBubble } from './MessageBubble'

interface ChatViewProps {
  messages: ChatMessage[]
  onCitationClick: (marker: number) => void
  activeCitation: number | null
  bottomRef: React.RefObject<HTMLDivElement | null>
}

export function ChatView({ messages, onCitationClick, activeCitation, bottomRef }: ChatViewProps) {
  return (
    <main className="chat">
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          message={m}
          onCitationClick={onCitationClick}
          activeCitation={activeCitation}
        />
      ))}
      <div ref={bottomRef} />
    </main>
  )
}
