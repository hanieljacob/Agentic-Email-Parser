import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, useCallback, useId, useEffect, useMemo } from 'react'
import {
  Paperclip,
  X,
  Send,
  File as FileIcon,
  Image as ImageIcon,
  FileText,
  Sheet,
  CheckCircle2,
  Loader2,
  Plus,
  Inbox,
} from 'lucide-react'

import { api } from '#/lib/api'
import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { Separator } from '#/components/ui/separator'
import { Textarea } from '#/components/ui/textarea'

export const Route = createFileRoute('/')({ component: ComposePage })

// ── helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function wordCount(text: string): number {
  return text.trim() === '' ? 0 : text.trim().split(/\s+/).length
}

/**
 * Attachments are grouped by how the extractor treats them, not by extension:
 * images go to the model as vision input, PDFs and spreadsheets are
 * text-extracted first, and anything else is skipped. "other" stays neutral
 * because that is the honest signal that nothing will be read from it.
 */
type FileKind = 'image' | 'pdf' | 'sheet' | 'other'

function fileKind(type: string, name: string): FileKind {
  if (type.startsWith('image/')) return 'image'
  if (type === 'application/pdf' || name.endsWith('.pdf')) return 'pdf'
  if (
    type.includes('spreadsheet') ||
    type.includes('excel') ||
    type === 'text/csv' ||
    /\.(xlsx?|csv)$/i.test(name)
  )
    return 'sheet'
  return 'other'
}

const KIND_ICON = {
  image: ImageIcon,
  pdf: FileText,
  sheet: Sheet,
  other: FileIcon,
} as const

const KIND_LABEL = {
  image: 'read as an image',
  pdf: 'text extracted',
  sheet: 'text extracted',
  other: 'not parsed',
} as const

// ── readiness ────────────────────────────────────────────────────────────────

/** Each segment fills green as its field is satisfied — state, not decoration. */
function Readiness({
  steps,
}: {
  steps: Array<{ label: string; done: boolean }>
}) {
  const filled = steps.filter((s) => s.done).length
  const ready = filled === steps.length

  return (
    <div className="flex items-center gap-2">
      <div
        className="flex gap-1"
        role="img"
        aria-label={`${filled} of ${steps.length} fields complete`}
      >
        {steps.map((step) => (
          <span
            key={step.label}
            title={step.label}
            className="block h-1 w-6 rounded-full transition-colors"
            style={{
              backgroundColor: step.done
                ? 'var(--conf-green)'
                : 'var(--border)',
            }}
          />
        ))}
      </div>
      <span
        className="text-xs font-medium tabular-nums"
        style={{ color: ready ? 'var(--conf-green)' : undefined }}
      >
        {ready ? 'Ready' : `${filled}/${steps.length}`}
      </span>
    </div>
  )
}

// ── attachment chip ──────────────────────────────────────────────────────────

function AttachmentChip({
  file,
  onRemove,
}: {
  file: File
  onRemove: () => void
}) {
  const [thumb, setThumb] = useState<string | null>(null)
  const kind = fileKind(file.type, file.name)
  const Icon = KIND_ICON[kind]

  useEffect(() => {
    if (!file.type.startsWith('image/')) return
    const url = URL.createObjectURL(file)
    setThumb(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  return (
    <div
      className={`file-${kind} flex items-center gap-2 overflow-hidden rounded-lg border border-border bg-card text-xs shadow-sm`}
      title={`${file.name} — ${KIND_LABEL[kind]}`}
    >
      {thumb ? (
        <img
          src={thumb}
          alt=""
          className="h-8 w-8 shrink-0 object-cover"
          // A corrupt image would otherwise render as a broken-image glyph.
          onError={() => setThumb(null)}
        />
      ) : (
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center"
          style={{
            backgroundColor:
              'color-mix(in oklab, var(--file-dot, var(--muted-foreground)) 14%, transparent)',
            color: 'var(--file-dot, var(--muted-foreground))',
          }}
        >
          <Icon className="h-3.5 w-3.5" />
        </span>
      )}

      <span className="max-w-[140px] truncate font-medium">{file.name}</span>
      <span className="text-muted-foreground">{formatBytes(file.size)}</span>

      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        className="mr-2 rounded p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  )
}

// ── result ───────────────────────────────────────────────────────────────────

function ProcessedConfirmation({
  to,
  subject,
  autoApplied,
  onNew,
}: {
  to: string
  subject: string
  /** Changes written back without review. Zero means nothing was extracted. */
  autoApplied: number
  onNew: () => void
}) {
  const applied = autoApplied > 0
  const accent = applied ? 'var(--conf-green)' : 'var(--muted-foreground)'
  const Icon = applied ? CheckCircle2 : Inbox

  return (
    <Card className="text-center">
      <CardHeader className="items-center">
        <div
          className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full"
          style={{
            backgroundColor: `color-mix(in oklab, ${accent} 12%, transparent)`,
            color: accent,
          }}
        >
          <Icon className="size-6" />
        </div>
        <CardTitle className="text-2xl">
          {applied
            ? `${autoApplied} change${autoApplied === 1 ? '' : 's'} applied automatically`
            : 'No PO changes detected'}
        </CardTitle>
        <CardDescription className="mx-auto max-w-md">
          {applied
            ? 'Every change scored at or above the auto-apply threshold, so nothing needed review. Each one is recorded in the audit log.'
            : 'The email was saved, but no actionable purchase order updates were found in it.'}
        </CardDescription>
      </CardHeader>

      <CardContent>
        <dl className="mx-auto max-w-sm divide-y divide-border overflow-hidden rounded-lg border border-border text-left text-sm">
          <div className="flex gap-3 px-4 py-2.5">
            <dt className="w-16 shrink-0 text-xs font-medium text-muted-foreground">
              To
            </dt>
            <dd className="truncate">{to}</dd>
          </div>
          <div className="flex gap-3 px-4 py-2.5">
            <dt className="w-16 shrink-0 text-xs font-medium text-muted-foreground">
              Subject
            </dt>
            <dd className="truncate">{subject}</dd>
          </div>
        </dl>
      </CardContent>

      <CardFooter className="justify-center">
        <Button onClick={onNew}>
          <Plus className="size-4" />
          Compose another
        </Button>
      </CardFooter>
    </Card>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

type Status = 'idle' | 'sending' | 'processing' | 'done' | 'error'

function bufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let binary = ''
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
  }
  return btoa(binary).replace(/.{76}/g, '$&\r\n')
}

function ComposePage() {
  const navigate = useNavigate()

  const fromId = useId()
  const toId = useId()
  const subjectId = useId()
  const bodyId = useId()

  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<Array<File>>([])
  const [dragging, setDragging] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [autoApplied, setAutoApplied] = useState(0)

  const canSend =
    from.trim() !== '' &&
    to.trim() !== '' &&
    subject.trim() !== '' &&
    body.trim() !== ''

  const steps = useMemo(
    () => [
      { label: 'Sender', done: from.trim() !== '' },
      { label: 'Recipient', done: to.trim() !== '' },
      { label: 'Subject', done: subject.trim() !== '' },
      { label: 'Message', done: body.trim().length > 20 },
    ],
    [from, to, subject, body],
  )

  const words = useMemo(() => wordCount(body), [body])
  const busy = status === 'sending' || status === 'processing'

  const addFiles = useCallback((files: FileList | Array<File> | null) => {
    if (!files || files.length === 0) return
    const incoming = Array.from(files)
    setAttachments((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}::${f.size}`))
      return [
        ...prev,
        ...incoming.filter((f) => !seen.has(`${f.name}::${f.size}`)),
      ]
    })
  }, [])

  async function doSend() {
    setStatus('sending')
    setErrorMsg(null)
    try {
      const date = new Date().toUTCString()
      const msgId = `<${crypto.randomUUID()}@compose.local>`
      const headers = [
        `From: ${from}`,
        `To: ${to}`,
        `Subject: ${subject}`,
        `Date: ${date}`,
        `Message-ID: ${msgId}`,
        `MIME-Version: 1.0`,
      ]

      let raw: string
      if (attachments.length === 0) {
        raw = [
          ...headers,
          `Content-Type: text/plain; charset=utf-8`,
          ``,
          body,
        ].join('\r\n')
      } else {
        const boundary = `----=_Part_${crypto.randomUUID().replace(/-/g, '')}`
        const textPart = [
          `--${boundary}`,
          `Content-Type: text/plain; charset=utf-8`,
          ``,
          body,
        ].join('\r\n')
        const parts = await Promise.all(
          attachments.map(async (file) => {
            const b64 = bufferToBase64(await file.arrayBuffer())
            return [
              `--${boundary}`,
              `Content-Type: ${file.type || 'application/octet-stream'}; name="${file.name}"`,
              `Content-Transfer-Encoding: base64`,
              `Content-Disposition: attachment; filename="${file.name}"`,
              ``,
              b64,
            ].join('\r\n')
          }),
        )
        raw = [
          ...headers,
          `Content-Type: multipart/mixed; boundary="${boundary}"`,
          ``,
          textPart,
          ...parts,
          `--${boundary}--`,
        ].join('\r\n')
      }

      setStatus('processing')
      const { email_id } = await api.ingestEmail(raw)
      const { pending, auto_applied } = await api.runPipeline(email_id)
      setAutoApplied(auto_applied)

      if (pending > 0) {
        await navigate({ to: '/review' })
      } else {
        setStatus('done')
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err))
      setStatus('error')
    }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSend && !busy) {
        void doSend()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  function handleReset() {
    setFrom('')
    setTo('')
    setSubject('')
    setBody('')
    setAttachments([])
    setStatus('idle')
    setErrorMsg(null)
    setAutoApplied(0)
  }

  if (status === 'done') {
    return (
      <main className="page-wrap px-4 pt-10 pb-16">
        <div className="mx-auto max-w-[720px]">
          <ProcessedConfirmation
            to={to}
            subject={subject}
            autoApplied={autoApplied}
            onNew={handleReset}
          />
        </div>
      </main>
    )
  }

  return (
    <main className="page-wrap px-4 pt-10 pb-16">
      <div className="mx-auto max-w-[720px]">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (canSend && !busy) void doSend()
          }}
          noValidate
        >
          <Card
            className={
              dragging
                ? 'outline-2 outline-offset-2 outline-[var(--ring)]'
                : undefined
            }
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node))
                setDragging(false)
            }}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              addFiles(e.dataTransfer.files)
            }}
          >
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>Compose supplier email</CardTitle>
                  <CardDescription>
                    Sent through the same pipeline a real inbound email takes.
                  </CardDescription>
                </div>
                <Readiness steps={steps} />
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              {errorMsg && (
                <p
                  className="rounded-lg border px-3 py-2 text-sm"
                  style={{
                    color: 'var(--destructive)',
                    borderColor:
                      'color-mix(in oklab, var(--destructive) 35%, transparent)',
                    backgroundColor:
                      'color-mix(in oklab, var(--destructive) 8%, transparent)',
                  }}
                >
                  {errorMsg}
                </p>
              )}

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor={fromId}>From</Label>
                  <Input
                    id={fromId}
                    type="email"
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                    placeholder="big@supplier.com"
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={toId}>To</Label>
                  <Input
                    id={toId}
                    type="email"
                    value={to}
                    onChange={(e) => setTo(e.target.value)}
                    placeholder="ops@acme.test"
                    autoComplete="off"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor={subjectId}>Subject</Label>
                <Input
                  id={subjectId}
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="PO-12 delivery confirmation"
                  autoComplete="off"
                />
              </div>

              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between">
                  <Label htmlFor={bodyId}>Message</Label>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {words} {words === 1 ? 'word' : 'words'}
                  </span>
                </div>
                <Textarea
                  id={bodyId}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={10}
                  placeholder={
                    'Include PO references, revised quantities or delivery dates.\n\ne.g. On SKU13 we can only ship 12000 units this quarter rather than the full 15000.'
                  }
                  className="resize-none"
                />
              </div>

              <Separator />

              <div className="space-y-3">
                {attachments.length > 0 && (
                  <div className="flex flex-wrap gap-2">
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

                <label
                  htmlFor="compose-file-input"
                  className={`flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed py-3 text-sm font-medium transition ${
                    dragging
                      ? 'border-[var(--ring)] bg-accent text-accent-foreground'
                      : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
                >
                  <Paperclip className="size-4" />
                  {dragging ? 'Drop to attach' : 'Attach files'}
                </label>
                <p className="text-center text-xs text-muted-foreground">
                  PDFs and spreadsheets are text-extracted; images go to the
                  model as vision input.
                </p>

                <input
                  id="compose-file-input"
                  type="file"
                  multiple
                  className="sr-only"
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? [])
                    e.target.value = ''
                    if (files.length) addFiles(files)
                  }}
                />
              </div>
            </CardContent>

            <CardFooter className="justify-between gap-3">
              <span className="text-xs text-muted-foreground">
                {busy
                  ? status === 'sending'
                    ? 'Ingesting email…'
                    : 'Running the extraction pipeline…'
                  : '⌘ ↵ to send'}
              </span>
              <Button type="submit" disabled={!canSend || busy}>
                {busy ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    {status === 'sending' ? 'Sending' : 'Analysing'}
                  </>
                ) : (
                  <>
                    <Send className="size-4" />
                    Send
                  </>
                )}
              </Button>
            </CardFooter>
          </Card>
        </form>
      </div>
    </main>
  )
}
