import { useEffect, useRef, useState } from 'react'

interface InputBarProps {
  onSend: (query: string, responseType?: string) => void
  onUpload: (files: File[]) => void
  disabled?: boolean
  uploading?: boolean
  deviceMode?: boolean
}

const TEMPLATES = [
  { label: '分段总-分', value: '请分段回答，先总后分' },
  { label: '步骤清单', value: '请给出步骤清单' },
  { label: '一句话', value: '用一句话概括' },
]

// 器械场景查询模板：点击填入输入框，光标停在开头待补全型号/报警码
const DEVICE_TEMPLATES = [
  { label: '报警含义', text: '的报警含义是什么？' },
  { label: '操作步骤', text: '的操作步骤是什么？' },
  { label: '规格参数', text: '的规格参数有哪些？' },
]

export function InputBar({ onSend, onUpload, disabled, uploading, deviceMode }: InputBarProps) {
  const [text, setText] = useState('')
  const [responseType, setResponseType] = useState('')
  const [showOpts, setShowOpts] = useState(false)
  const [pendingCursor, setPendingCursor] = useState<number | null>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }, [text])

  useEffect(() => {
    if (pendingCursor === null) return
    const ta = taRef.current
    if (ta) {
      ta.focus()
      ta.setSelectionRange(pendingCursor, pendingCursor)
    }
    setPendingCursor(null)
  }, [pendingCursor])

  const applyTemplate = (t: { text: string }) => {
    if (disabled) return
    setText(t.text)
    setPendingCursor(0)
  }

  const handleSend = () => {
    const q = text.trim()
    if (!q || disabled) return
    onSend(q, responseType || undefined)
    setText('')
  }

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length > 0) onUpload(files)
    e.target.value = '' // 允许再次选择同一文件
  }

  const attachDisabled = disabled || uploading

  return (
    <div className="input-area">
      {deviceMode && (
        <div className="scene-row">
          <span className="scene-label">器械场景</span>
          {DEVICE_TEMPLATES.map((t) => (
            <span key={t.label} className="chip" onClick={() => applyTemplate(t)}>
              {t.label}
            </span>
          ))}
        </div>
      )}
      <div className="opt-row">
        <button className="link-btn" onClick={() => setShowOpts((s) => !s)}>
          回答要求 {showOpts ? '▴' : '▾'}
        </button>
        {showOpts && (
          <span className="tpl">
            {TEMPLATES.map((t) => (
              <span
                key={t.value}
                className={`chip ${responseType === t.value ? 'active' : ''}`}
                onClick={() => setResponseType(responseType === t.value ? '' : t.value)}
              >
                {t.label}
              </span>
            ))}
          </span>
        )}
      </div>
      <div className="input-row">
        <label
          className={`attach-btn${attachDisabled ? ' disabled' : ''}`}
          title='上传文档（pdf/docx/md，可多选，入库后可提问）'
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.md"
            multiple
            hidden
            disabled={attachDisabled}
            onChange={handleFile}
          />
        </label>
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          rows={1}
        />
        <button className="send-btn" onClick={handleSend} disabled={disabled || !text.trim()}>
          发送
        </button>
      </div>
    </div>
  )
}
