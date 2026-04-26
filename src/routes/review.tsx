import { createFileRoute } from '@tanstack/react-router'
import { createServerFn } from '@tanstack/react-start'
import { useState } from 'react'
import pg from 'pg'
import { applyProposedChange } from '../writeback/apply.js'

const { Pool } = pg
const pool = new Pool({ connectionString: process.env.DATABASE_URL })

// ── server functions ──────────────────────────────────────────────────────────

const listPending = createServerFn({ method: 'GET' }).handler(async () => {
  const res = await pool.query<{
    id: string
    field_name: string
    old_value: string
    new_value: string
    evidence_text: string
    email_id: string
    sender: string
    subject: string
    po_reference: string
    product_sku: string
    product_title: string
  }>(`
    SELECT
      pc.id,
      pc.field_name,
      pc.old_value,
      pc.new_value,
      pc.evidence_text,
      pc.email_id,
      e.sender,
      e.subject,
      po.reference_num AS po_reference,
      p.sku           AS product_sku,
      p.title         AS product_title
    FROM proposed_changes pc
    JOIN emails e              ON e.id   = pc.email_id
    JOIN purchase_order_line pol ON pol.id = pc.target_record_id
    JOIN purchase_order po     ON po.id  = pol.purchase_order_id
    JOIN product p             ON p.id   = pol.product_id
    WHERE pc.status = 'pending'
    ORDER BY pc.created_at DESC
  `)
  return res.rows
})

const applyChange = createServerFn({ method: 'POST' })
  .inputValidator((id: string) => id)
  .handler(async ({ data: id }) => {
    await pool.query(
      `UPDATE proposed_changes SET status = 'approved' WHERE id = $1 AND status = 'pending'`,
      [id],
    )
    return applyProposedChange(id, 'reviewer')
  })

const rejectChange = createServerFn({ method: 'POST' })
  .inputValidator((id: string) => id)
  .handler(async ({ data: id }) => {
    await pool.query(
      `UPDATE proposed_changes SET status = 'rejected' WHERE id = $1`,
      [id],
    )
    return { ok: true }
  })

// ── route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/review')({
  loader: () => listPending(),
  component: ReviewPage,
})

type Change = Awaited<ReturnType<typeof listPending>>[number]

function ChangeCard({ change, onDone }: { change: Change; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  async function handle(action: 'apply' | 'reject') {
    setBusy(true)
    try {
      if (action === 'apply') {
        const r = await applyChange({ data: change.id })
        setResult(r.status === 'superseded' ? 'Superseded — record changed before apply.' : 'Applied')
      } else {
        await rejectChange({ data: change.id })
        setResult('Rejected')
      }
      setTimeout(onDone, 800)
    } catch (err) {
      setResult(`Error: ${err instanceof Error ? err.message : String(err)}`)
      setBusy(false)
    }
  }

  const senderName = change.sender.replace(/<[^>]+>/, '').trim() || change.sender

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--card-bg,var(--bg))] p-5 shadow-sm">
      {/* header row */}
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs text-[var(--muted)] mb-0.5">{senderName}</p>
          <p className="font-semibold leading-tight">{change.subject || '(no subject)'}</p>
        </div>
        <span className="rounded-full bg-[var(--chip-bg)] px-2.5 py-0.5 text-xs font-medium text-[var(--sea-ink)] border border-[var(--chip-line)]">
          PO {change.po_reference}
        </span>
      </div>

      {/* change details */}
      <div className="mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <span className="text-[var(--muted)]">Product</span>
        <span>{change.product_title} <span className="text-[var(--muted)]">({change.product_sku})</span></span>

        <span className="text-[var(--muted)]">Field</span>
        <span className="font-mono">{change.field_name}</span>

        <span className="text-[var(--muted)]">Change</span>
        <span>
          <span className="line-through text-[var(--muted)]">{change.old_value}</span>
          {' → '}
          <span className="font-semibold text-[var(--lagoon-deep)]">{change.new_value}</span>
        </span>
      </div>

      {/* evidence */}
      {change.evidence_text && (
        <blockquote className="mb-4 border-l-2 border-[var(--line)] pl-3 text-sm italic text-[var(--muted)]">
          "{change.evidence_text}"
        </blockquote>
      )}

      {/* actions */}
      {result ? (
        <p className="text-sm font-medium text-[var(--lagoon-deep)]">{result}</p>
      ) : (
        <div className="flex gap-2">
          <button
            disabled={busy}
            onClick={() => handle('apply')}
            className="rounded-lg bg-[var(--lagoon-deep)] px-4 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            disabled={busy}
            onClick={() => handle('reject')}
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
  const [changes, setChanges] = useState(initial)

  if (changes.length === 0) {
    return (
      <main className="page-wrap py-12 text-center text-[var(--muted)]">
        No pending changes. Run <code className="rounded bg-[var(--chip-bg)] px-1">pnpm extract</code> then{' '}
        <code className="rounded bg-[var(--chip-bg)] px-1">pnpm match</code> after ingesting an email.
      </main>
    )
  }

  return (
    <main className="page-wrap py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold">Pending Review</h1>
        <span className="rounded-full bg-[var(--chip-bg)] px-3 py-1 text-xs font-semibold border border-[var(--chip-line)]">
          {changes.length} change{changes.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="flex flex-col gap-4">
        {changes.map((c) => (
          <ChangeCard
            key={c.id}
            change={c}
            onDone={() => setChanges((prev) => prev.filter((x) => x.id !== c.id))}
          />
        ))}
      </div>
    </main>
  )
}
