import { createFileRoute } from '@tanstack/react-router'
import {
  useState,
  useRef,
  useCallback,
  useId,
  useEffect,
  useMemo,
} from 'react'
import {
  Paperclip,
  X,
  Send,
  File,
  Image,
  FileText,
  Sheet,
  CheckCircle2,
  Loader2,
  Plus,
  ArrowUpRight,
} from 'lucide-react'

export const Route = createFileRoute('/')({ component: ComposePage })

// ─── helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function wordCount(text: string): number {
  return text.trim() === '' ? 0 : text.trim().split(/\s+/).length
}

// ─── completeness dots ────────────────────────────────────────────────────────

function CompletenessBar({
  steps,
}: {
  steps: { label: string; done: boolean }[]
}) {
  const filled = steps.filter((s) => s.done).length
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-1">
        {steps.map((step) => (
          <span
            key={step.label}
            title={step.label}
            className={`block h-1.5 w-5 rounded-full transition-all duration-300 ${
              step.done
                ? 'bg-[var(--lagoon-deep)] shadow-[0_0_6px_rgba(37,99,235,0.4)]'
                : 'bg-[var(--line)]'
            }`}
          />
        ))}
      </div>
      <span className="text-[10px] font-semibold tabular-nums text-[var(--sea-ink-soft)]/40">
        {filled}/{steps.length}
      </span>
    </div>
  )
}

// ─── field row ────────────────────────────────────────────────────────────────

function FieldRow({
  label,
  htmlFor,
  aside,
  children,
}: {
  label: string
  htmlFor: string
  aside?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="group/field relative flex items-center border-b border-[var(--line)] transition-colors focus-within:border-[color-mix(in_oklab,var(--lagoon)_50%,var(--line))]">
      {/* left accent bar */}
      <span
        aria-hidden
        className="absolute left-0 top-0 h-full w-[2px] origin-center scale-y-0 rounded-r bg-[var(--lagoon-deep)] transition-transform duration-200 group-focus-within/field:scale-y-100"
      />

      <label
        htmlFor={htmlFor}
        className="w-[4.5rem] flex-shrink-0 cursor-pointer select-none px-5 py-3.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[var(--sea-ink-soft)]/50 transition-colors group-focus-within/field:text-[var(--lagoon-deep)]"
      >
        {label}
      </label>

      <div className="flex flex-1 items-center py-3.5 pr-4">{children}</div>

      {aside && <div className="pr-4">{aside}</div>}
    </div>
  )
}

// ─── attachment chip ──────────────────────────────────────────────────────────

function AttachmentChip({
  file,
  onRemove,
}: {
  file: File
  onRemove: () => void
}) {
  const [thumb, setThumb] = useState<string | null>(null)

  useEffect(() => {
    if (!file.type.startsWith('image/')) return
    const url = URL.createObjectURL(file)
    setThumb(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function Icon() {
    if (file.type.startsWith('image/')) return <Image className="h-3.5 w-3.5" />
    if (file.type === 'application/pdf') return <FileText className="h-3.5 w-3.5" />
    if (
      file.type.includes('spreadsheet') ||
      file.type.includes('excel') ||
      file.type === 'text/csv'
    )
      return <Sheet className="h-3.5 w-3.5" />
    return <File className="h-3.5 w-3.5" />
  }

  return (
    <div className="group/chip flex items-center gap-2 overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--surface)] text-xs shadow-sm transition hover:border-[color-mix(in_oklab,var(--lagoon)_30%,var(--line))] hover:shadow-md">
      {/* thumbnail or icon */}
      {thumb ? (
        <img
          src={thumb}
          alt=""
          className="h-8 w-8 flex-shrink-0 object-cover"
        />
      ) : (
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center bg-[rgba(59,130,246,0.08)] text-[var(--lagoon-deep)]">
          <Icon />
        </span>
      )}

      <span className="max-w-[120px] truncate font-medium text-[var(--sea-ink)]">
        {file.name}
      </span>
      <span className="text-[var(--sea-ink-soft)]/50">{formatBytes(file.size)}</span>

      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        className="mr-2 rounded p-0.5 text-[var(--sea-ink-soft)]/35 transition hover:bg-[rgba(0,0,0,0.06)] hover:text-[var(--sea-ink)]"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  )
}

// ─── sent confirmation ────────────────────────────────────────────────────────

function SentConfirmation({
  to,
  subject,
  attachmentCount,
  onNew,
}: {
  to: string
  subject: string
  attachmentCount: number
  onNew: () => void
}) {
  return (
    <div className="island-shell rise-in overflow-hidden rounded-2xl">
      {/* top accent */}
      <div className="h-1 w-full bg-[linear-gradient(90deg,var(--lagoon-deep),var(--lagoon),#93c5fd)]" />

      <div className="px-8 py-14 text-center">
        <div className="mb-6 flex justify-center">
          <div className="relative flex h-16 w-16 items-center justify-center">
            <span className="absolute inset-0 animate-ping rounded-full bg-[rgba(59,130,246,0.15)]" />
            <div className="relative flex h-16 w-16 items-center justify-center rounded-full bg-[rgba(59,130,246,0.10)] ring-1 ring-[rgba(59,130,246,0.22)]">
              <CheckCircle2 className="h-8 w-8 text-[var(--lagoon-deep)]" />
            </div>
          </div>
        </div>

        <p className="island-kicker mb-2">Email submitted</p>
        <h2 className="display-title m-0 mb-2 text-2xl font-bold text-[var(--sea-ink)]">
          Queued for parsing
        </h2>
        <p className="mb-8 text-sm text-[var(--sea-ink-soft)]/70">
          The LLM pipeline will extract PO data and update the database.
        </p>

        <div className="mx-auto mb-8 max-w-sm divide-y divide-[var(--line)] overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--surface)] text-left text-sm">
          <SummaryRow label="To" value={to} />
          <SummaryRow label="Subject" value={subject} />
          {attachmentCount > 0 && (
            <SummaryRow
              label="Files"
              value={`${attachmentCount} attachment${attachmentCount !== 1 ? 's' : ''}`}
            />
          )}
        </div>

        <button
          onClick={onNew}
          className="inline-flex items-center gap-2 rounded-full bg-[var(--lagoon-deep)] px-6 py-2.5 text-sm font-semibold text-white shadow-[0_4px_14px_rgba(37,99,235,0.25)] transition hover:-translate-y-0.5 hover:shadow-[0_6px_20px_rgba(37,99,235,0.30)]"
        >
          <Plus className="h-4 w-4" />
          Compose another
        </button>
      </div>
    </div>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 px-4 py-3">
      <span className="w-14 flex-shrink-0 text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--sea-ink-soft)]/50">
        {label}
      </span>
      <span className="truncate text-[var(--sea-ink)]">{value}</span>
    </div>
  )
}

// ─── page ─────────────────────────────────────────────────────────────────────

type Status = 'idle' | 'sending' | 'sent'

function ComposePage() {
  const toId = useId()
  const ccId = useId()
  const subjectId = useId()
  const bodyId = useId()

  const [to, setTo] = useState('')
  const [cc, setCc] = useState('')
  const [showCc, setShowCc] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [status, setStatus] = useState<Status>('idle')

  const fileInputRef = useRef<HTMLInputElement>(null)

  const canSend =
    to.trim() !== '' && subject.trim() !== '' && body.trim() !== ''

  const completenessSteps = useMemo(
    () => [
      { label: 'Recipient (To)', done: to.trim() !== '' },
      { label: 'Subject line', done: subject.trim() !== '' },
      { label: 'Message body', done: body.trim().length > 20 },
      { label: 'Attachments', done: attachments.length > 0 },
    ],
    [to, subject, body, attachments.length],
  )

  const wc = useMemo(() => wordCount(body), [body])

  // ⌘/Ctrl + Enter to send
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        if (canSend && status === 'idle') {
          void doSend()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const addFiles = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return
    setAttachments((prev) => {
      const existing = new Set(prev.map((f) => `${f.name}::${f.size}`))
      const incoming = Array.from(files).filter(
        (f) => !existing.has(`${f.name}::${f.size}`),
      )
      return [...prev, ...incoming]
    })
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragging(false)
    }
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      addFiles(e.dataTransfer.files)
    },
    [addFiles],
  )

  async function doSend() {
    setStatus('sending')
    const date = new Date().toUTCString()
    const msgId = `<${crypto.randomUUID()}@compose.local>`
    const raw = [
      `From: compose@local`,
      `To: ${to}`,
      ...(cc.trim() ? [`Cc: ${cc}`] : []),
      `Subject: ${subject}`,
      `Date: ${date}`,
      `Message-ID: ${msgId}`,
      `MIME-Version: 1.0`,
      `Content-Type: text/plain; charset=utf-8`,
      ``,
      body,
    ].join('\r\n')

    const res = await fetch('http://localhost:8000/emails', {
      method: 'POST',
      body: raw,
    })
    if (!res.ok) throw new Error(`Ingest failed: ${res.status}`)
    setStatus('sent')
  }

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault()
    if (status !== 'idle' || !canSend) return
    await doSend()
  }

  const handleReset = () => {
    setTo('')
    setCc('')
    setShowCc(false)
    setSubject('')
    setBody('')
    setAttachments([])
    setStatus('idle')
  }

  if (status === 'sent') {
    return (
      <main className="page-wrap px-4 pb-16 pt-10">
        <div className="mx-auto max-w-[720px]">
          <SentConfirmation
            to={to}
            subject={subject}
            attachmentCount={attachments.length}
            onNew={handleReset}
          />
        </div>
      </main>
    )
  }

  return (
    <main className="page-wrap px-4 pb-16 pt-10">
      <div className="mx-auto max-w-[720px]">
        <form onSubmit={handleSubmit} noValidate>
          <div
            className={`island-shell rise-in overflow-hidden rounded-2xl outline outline-2 transition-[outline-color] duration-150 ${
              dragging ? 'outline-[var(--lagoon)]' : 'outline-transparent'
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            {/* ── top bar ─────────────────────────────────────────────────── */}
            <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-4">
              <div className="space-y-2">
                <CompletenessBar steps={completenessSteps} />
                <h1 className="display-title m-0 text-xl font-bold leading-tight text-[var(--sea-ink)]">
                  Compose Email
                </h1>
              </div>

              <div className="flex flex-col items-end gap-1.5">
                <button
                  type="submit"
                  disabled={!canSend || status === 'sending'}
                  className="group flex items-center gap-2 rounded-full bg-[var(--lagoon-deep)] px-5 py-2.5 text-sm font-semibold text-white shadow-[0_4px_14px_rgba(37,99,235,0.25)] transition enabled:hover:-translate-y-0.5 enabled:hover:shadow-[0_6px_20px_rgba(37,99,235,0.32)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {status === 'sending' ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Sending…
                    </>
                  ) : (
                    <>
                      <Send className="h-4 w-4 transition group-enabled:group-hover:translate-x-0.5" />
                      Send
                      <ArrowUpRight className="h-3.5 w-3.5 opacity-60" />
                    </>
                  )}
                </button>
                <span className="text-[10px] text-[var(--sea-ink-soft)]/35">
                  ⌘ ↵ to send
                </span>
              </div>
            </div>

            {/* ── address & subject ────────────────────────────────────────── */}
            <FieldRow
              label="To"
              htmlFor={toId}
              aside={
                <button
                  type="button"
                  onClick={() => setShowCc((v) => !v)}
                  className="text-[10px] font-bold uppercase tracking-[0.15em] text-[var(--sea-ink-soft)]/40 transition hover:text-[var(--lagoon-deep)]"
                >
                  {showCc ? 'Hide CC' : '+ CC'}
                </button>
              }
            >
              <input
                id={toId}
                type="email"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="supplier@company.com"
                required
                className="w-full bg-transparent text-sm text-[var(--sea-ink)] placeholder:text-[var(--sea-ink-soft)]/35 outline-none"
              />
            </FieldRow>

            {showCc && (
              <FieldRow label="CC" htmlFor={ccId}>
                <input
                  id={ccId}
                  type="email"
                  value={cc}
                  onChange={(e) => setCc(e.target.value)}
                  placeholder="cc@company.com"
                  className="w-full bg-transparent text-sm text-[var(--sea-ink)] placeholder:text-[var(--sea-ink-soft)]/35 outline-none"
                />
              </FieldRow>
            )}

            <FieldRow label="Subject" htmlFor={subjectId}>
              <input
                id={subjectId}
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="e.g. PO-2024-001 – Delivery date update"
                required
                className="w-full bg-transparent text-sm text-[var(--sea-ink)] placeholder:text-[var(--sea-ink-soft)]/35 outline-none"
              />
            </FieldRow>

            {/* ── body ─────────────────────────────────────────────────────── */}
            <div className="group/body relative">
              <label htmlFor={bodyId} className="sr-only">
                Message body
              </label>
              <textarea
                id={bodyId}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder={
                  'Write your message…\n\nInclude PO numbers, updated quantities, revised delivery dates, or any supplier notes. Attachments such as PDFs, images, and spreadsheets will also be parsed.'
                }
                rows={13}
                required
                className="w-full resize-none bg-transparent px-5 py-4 text-sm leading-relaxed text-[var(--sea-ink)] placeholder:text-[var(--sea-ink-soft)]/28 outline-none"
              />

              {/* word count */}
              <div
                className={`absolute bottom-3 right-4 text-[11px] tabular-nums transition ${
                  wc > 0
                    ? 'text-[var(--sea-ink-soft)]/40'
                    : 'text-transparent'
                }`}
              >
                {wc} {wc === 1 ? 'word' : 'words'}
              </div>
            </div>

            {/* ── attachments ──────────────────────────────────────────────── */}
            <div className="border-t border-[var(--line)] px-5 pb-5 pt-4">
              {attachments.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-2">
                  {attachments.map((file, i) => (
                    <AttachmentChip
                      key={`${file.name}::${file.size}::${i}`}
                      file={file}
                      onRemove={() =>
                        setAttachments((prev) =>
                          prev.filter((_, idx) => idx !== i),
                        )
                      }
                    />
                  ))}
                </div>
              )}

              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className={`flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed py-3 text-sm font-medium transition ${
                  dragging
                    ? 'border-[var(--lagoon)] bg-[rgba(59,130,246,0.08)] text-[var(--lagoon-deep)]'
                    : 'border-[var(--line)] text-[var(--sea-ink-soft)]/55 hover:border-[color-mix(in_oklab,var(--lagoon)_38%,var(--line))] hover:bg-[rgba(59,130,246,0.05)] hover:text-[var(--sea-ink-soft)]'
                }`}
              >
                <Paperclip className="h-4 w-4" />
                {dragging ? 'Drop to attach' : 'Attach files'}
                {!dragging && (
                  <span className="text-xs font-normal opacity-50">
                    · drag &amp; drop or click
                  </span>
                )}
              </button>

              <p className="mt-2 text-center text-[11px] text-[var(--sea-ink-soft)]/35">
                PDF, images, spreadsheets, Word docs — any PO-related documents
              </p>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  addFiles(e.target.files)
                  e.target.value = ''
                }}
              />
            </div>
          </div>
        </form>
      </div>
    </main>
  )
}
