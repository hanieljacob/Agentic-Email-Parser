import { createFileRoute, useRouter } from '@tanstack/react-router'
import { useState } from 'react'
import {
  api,
  REJECTION_REASONS,
  type ProposedChange,
  type RejectionReason,
} from '#/lib/api'

export const Route = createFileRoute('/review')({
  loader: () => api.listProposedChanges(),
  component: ReviewPage,
})

function ChangeCard({
  change,
  onDone,
}: {
  change: ProposedChange
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectionReason, setRejectionReason] = useState<RejectionReason>('other')

  async function handleApprove() {
    setBusy(true)
    try {
      const r = await api.approveChange(change.id)
      setResult(
        r.status === 'superseded'
          ? 'Superseded — record changed before apply.'
          : 'Applied',
      )
      setTimeout(onDone, 800)
    } catch (err) {
      setResult(`Error: ${err instanceof Error ? err.message : String(err)}`)
      setBusy(false)
    }
  }

  async function handleReject() {
    setBusy(true)
    try {
      await api.rejectChange(change.id, rejectionReason)
      setResult('Rejected')
      setTimeout(onDone, 800)
    } catch (err) {
      setResult(`Error: ${err instanceof Error ? err.message : String(err)}`)
      setBusy(false)
    }
  }

  const senderName = change.sender.replace(/<[^>]+>/, '').trim() || change.sender

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--card-bg,var(--bg))] p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="mb-0.5 text-xs text-[var(--muted)]">{senderName}</p>
          <p className="font-semibold leading-tight">
            {change.subject || '(no subject)'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-[var(--chip-line)] bg-[var(--chip-bg)] px-2.5 py-0.5 text-xs font-medium text-[var(--sea-ink)]">
            PO {change.po_reference}
          </span>
          <span className="rounded-full border border-[var(--chip-line)] px-2.5 py-0.5 text-xs font-semibold tabular-nums">
            {(change.combined_confidence * 100).toFixed(0)}%
          </span>
        </div>
      </div>

      <div className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <span className="text-[var(--muted)]">Product</span>
        <span>
          {change.product_title}{' '}
          <span className="text-[var(--muted)]">({change.product_sku})</span>
        </span>

        <span className="text-[var(--muted)]">Field</span>
        <span className="font-mono">{change.field_name}</span>

        <span className="text-[var(--muted)]">Change</span>
        <span>
          <span className="text-[var(--muted)] line-through">
            {change.old_value}
          </span>
          {' → '}
          <span className="font-semibold text-[var(--lagoon-deep)]">
            {change.new_value}
          </span>
        </span>
      </div>

      {change.evidence_text && (
        <blockquote className="mb-4 border-l-2 border-[var(--line)] pl-3 text-sm italic text-[var(--muted)]">
          "{change.evidence_text}"
        </blockquote>
      )}

      {result ? (
        <p className="text-sm font-medium text-[var(--lagoon-deep)]">{result}</p>
      ) : rejectOpen ? (
        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium text-[var(--muted)]">
            Reason for rejection
          </label>
          <select
            value={rejectionReason}
            onChange={(e) =>
              setRejectionReason(e.target.value as RejectionReason)
            }
            className="rounded-lg border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 text-sm"
          >
            {REJECTION_REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={handleReject}
              className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Confirm reject
            </button>
            <button
              disabled={busy}
              onClick={() => setRejectOpen(false)}
              className="rounded-lg border border-[var(--line)] px-4 py-1.5 text-sm font-semibold hover:bg-[var(--chip-bg)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <button
            disabled={busy}
            onClick={handleApprove}
            className="rounded-lg bg-[var(--lagoon-deep)] px-4 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            disabled={busy}
            onClick={() => setRejectOpen(true)}
            className="rounded-lg border border-[var(--line)] px-4 py-1.5 text-sm font-semibold hover:bg-[var(--chip-bg)] disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      )}
    </div>
  )
}

function ReviewPage() {
  const initial = Route.useLoaderData()
  const router = useRouter()
  const [changes, setChanges] = useState(initial)

  function dismiss(id: string) {
    setChanges((prev) => prev.filter((c) => c.id !== id))
    void router.invalidate()
  }

  if (changes.length === 0) {
    return (
      <main className="page-wrap py-12 text-center text-[var(--muted)]">
        No pending changes. Run{' '}
        <code className="rounded bg-[var(--chip-bg)] px-1">pnpm seed</code> to
        load the demo dataset, or send an email from the compose page.
      </main>
    )
  }

  return (
    <main className="page-wrap py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">Pending Review</h1>
        <span className="rounded-full border border-[var(--chip-line)] bg-[var(--chip-bg)] px-3 py-1 text-xs font-semibold">
          {changes.length} change{changes.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="flex flex-col gap-4">
        {changes.map((c) => (
          <ChangeCard key={c.id} change={c} onDone={() => dismiss(c.id)} />
        ))}
      </div>
    </main>
  )
}
