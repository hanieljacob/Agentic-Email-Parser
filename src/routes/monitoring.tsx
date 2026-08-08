import { createFileRoute } from '@tanstack/react-router'
import { api } from '#/lib/api'
import type { MonitoringData } from '#/lib/api'

// ── route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute('/monitoring')({
  loader: () => api.getMonitoring(),
  component: MonitoringPage,
})

// ── helpers ───────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diffMs / 3_600_000)
  const m = Math.floor((diffMs % 3_600_000) / 60_000)
  if (h > 0) return `${h}h ${m}m ago`
  return `${m}m ago`
}

const STATUS_ORDER = [
  'matched',
  'needs_review',
  'extracted',
  'ingested',
  'failed',
]
const STATUS_STYLE: Record<string, string> = {
  matched: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  needs_review:
    'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  extracted: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  ingested: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
}

const REASON_LABEL: Record<string, string> = {
  wrong_date_format: 'Wrong date format',
  wrong_sku: 'Wrong SKU',
  not_a_po_update: 'Not a PO update',
  quantity_is_delta: 'Qty is delta',
  wrong_po_reference: 'Wrong PO ref',
  llm_hallucination: 'Hallucination',
  other: 'Other',
}

// ── components ────────────────────────────────────────────────────────────────

type Data = MonitoringData

function Section({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-card p-5 shadow-sm">
      {children}
    </div>
  )
}

function PipelineHealth({
  statusCounts,
  changesSummary,
}: {
  statusCounts: Data['status_counts']
  changesSummary: Data['changes_summary']
}) {
  // Only statuses actually present come back from the view, so a lookup can
  // miss — Object.fromEntries would otherwise type every key as present.
  const byStatus: Record<string, number | undefined> = Object.fromEntries(
    statusCounts.map((r) => [r.status, r.count]),
  )
  const sorted = STATUS_ORDER.filter((s) => byStatus[s] != null).concat(
    statusCounts.map((r) => r.status).filter((s) => !STATUS_ORDER.includes(s)),
  )

  const hasFailed = (byStatus.failed ?? 0) > 0
  const hasIngested = (byStatus.ingested ?? 0) > 0
  const health = hasFailed ? 'red' : hasIngested ? 'yellow' : 'green'

  return (
    <Card>
      <div className="mb-4 flex items-center gap-2">
        <span
          className={`h-3 w-3 rounded-full ${
            health === 'green'
              ? 'bg-green-500'
              : health === 'yellow'
                ? 'bg-yellow-500'
                : 'bg-red-500'
          }`}
        />
        <span className="text-sm font-medium">
          {health === 'green'
            ? 'All emails processed'
            : health === 'yellow'
              ? 'Emails awaiting processing'
              : 'Extraction failures present'}
        </span>
      </div>

      <div className="flex flex-wrap gap-2 mb-5">
        {sorted.map((status) => (
          <span
            key={status}
            className={`rounded-full px-3 py-1 text-xs font-semibold ${STATUS_STYLE[status] ?? 'bg-gray-100 text-gray-700'}`}
          >
            {status}: {byStatus[status]}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {[
          { label: 'Pending review', value: changesSummary.pending },
          { label: 'Auto-applied', value: changesSummary.total_applied },
          { label: 'Rejected', value: changesSummary.total_rejected },
          {
            label: 'Avg confidence',
            value: changesSummary.avg_confidence ?? '—',
          },
        ].map(({ label, value }) => (
          <div
            key={label}
            className="rounded-lg border border-[var(--line)] p-3"
          >
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-xl font-bold mt-0.5">{value}</p>
          </div>
        ))}
      </div>
    </Card>
  )
}

function StuckEmails({ emails }: { emails: Data['stuck_emails'] }) {
  if (emails.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted-foreground">
          No stuck emails — pipeline is healthy.
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground border-b border-[var(--line)]">
              <th className="pb-2 pr-4 font-medium">Sender</th>
              <th className="pb-2 pr-4 font-medium">Subject</th>
              <th className="pb-2 pr-4 font-medium">Status</th>
              <th className="pb-2 pr-4 font-medium">Age</th>
              <th className="pb-2 font-medium">Attempts</th>
            </tr>
          </thead>
          <tbody>
            {emails.map((e) => (
              <tr
                key={e.id}
                className="border-b border-[var(--line)] last:border-0"
              >
                <td className="py-2 pr-4 font-mono text-xs max-w-[180px] truncate">
                  {e.sender.replace(/<[^>]+>/, '').trim() || e.sender}
                </td>
                <td className="py-2 pr-4 text-muted-foreground max-w-[220px] truncate">
                  {e.subject || '(no subject)'}
                </td>
                <td className="py-2 pr-4">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_STYLE[e.status] ?? ''}`}
                  >
                    {e.status}
                  </span>
                </td>
                <td className="py-2 pr-4 text-muted-foreground">
                  {timeAgo(e.received_at)}
                </td>
                <td className="py-2 text-muted-foreground">
                  {e.attempt_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        The retry worker automatically re-triggers these every{' '}
        <code className="rounded bg-[var(--chip-bg)] px-1">
          WORKER_INTERVAL_SECONDS
        </code>{' '}
        seconds.
      </p>
    </Card>
  )
}

function RejectionPatterns({ rejections }: { rejections: Data['rejections'] }) {
  if (rejections.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted-foreground">
          No rejections with reasons recorded yet.
        </p>
      </Card>
    )
  }

  // Group by supplier
  const bySupplier: Record<string, typeof rejections> = {}
  for (const r of rejections) {
    ;(bySupplier[r.supplier_name] ??= []).push(r)
  }

  return (
    <div className="flex flex-col gap-4">
      {Object.entries(bySupplier).map(([supplier, rows]) => {
        const hasNotes = rows[0].has_notes
        const total = rows.reduce((s, r) => s + r.count, 0)
        return (
          <Card key={supplier}>
            <div className="mb-3 flex items-center justify-between gap-2">
              <span className="font-semibold">{supplier}</span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {total} rejection{total !== 1 ? 's' : ''}
                </span>
                {hasNotes ? (
                  <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900 dark:text-green-200">
                    notes set
                  </span>
                ) : (
                  <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200">
                    no notes yet
                  </span>
                )}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {rows.map((r) => (
                <span
                  key={r.rejection_reason}
                  className="rounded-full border border-[var(--line)] bg-[var(--chip-bg)] px-3 py-1 text-xs"
                >
                  {REASON_LABEL[r.rejection_reason] ?? r.rejection_reason}:{' '}
                  <strong>{r.count}</strong>
                </span>
              ))}
            </div>
            {!hasNotes && total >= 3 && (
              <p className="mt-3 text-xs text-yellow-700 dark:text-yellow-300">
                Consider setting{' '}
                <code className="rounded bg-[var(--chip-bg)] px-1">
                  supplier.llm_notes
                </code>{' '}
                to address the pattern above.
              </p>
            )}
          </Card>
        )
      })}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

function MonitoringPage() {
  const data = Route.useLoaderData()
  return (
    <main className="page-wrap py-8">
      <h1 className="mb-6 text-xl font-bold">Monitoring</h1>

      <Section title="Pipeline Health">
        <PipelineHealth
          statusCounts={data.status_counts}
          changesSummary={data.changes_summary}
        />
      </Section>

      <Section
        title={`Stuck Emails${data.stuck_emails.length > 0 ? ` (${data.stuck_emails.length})` : ''}`}
      >
        <StuckEmails emails={data.stuck_emails} />
      </Section>

      <Section title="Rejection Patterns by Supplier">
        <RejectionPatterns rejections={data.rejections} />
      </Section>
    </main>
  )
}
