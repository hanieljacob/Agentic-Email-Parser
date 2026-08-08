import { createFileRoute, useRouter } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowRight, Inbox, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { api, REJECTION_REASONS } from '#/lib/api'
import type { MonitoringData, ProposedChange, RejectionReason } from '#/lib/api'
import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Label } from '#/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Separator } from '#/components/ui/separator'
import { Skeleton } from '#/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'

export const Route = createFileRoute('/review')({
  loader: async () => {
    const [changes, monitoring] = await Promise.all([
      api.listProposedChanges(),
      api.getMonitoring(),
    ])
    return { changes, summary: monitoring.changes_summary }
  },
  component: ReviewPage,
  pendingComponent: ReviewSkeleton,
  errorComponent: ReviewError,
})

// ── helpers ──────────────────────────────────────────────────────────────────

function senderName(sender: string): string {
  return sender.replace(/<[^>]+>/, '').trim() || sender
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

/**
 * Values are stored as text because a proposed change can target either a
 * date or a numeric column. Postgres renders numeric(18,4) with its full
 * scale, so a quantity of 15000 comes back as "15000.0000" — correct, but
 * not what a reviewer should have to read.
 */
function formatValue(value: string | null): string {
  if (value === null || value === '') return '—'
  const asNumber = Number(value)
  if (!Number.isNaN(asNumber) && /^-?\d+(\.\d+)?$/.test(value)) {
    return asNumber.toLocaleString(undefined, { maximumFractionDigits: 4 })
  }
  return value
}

/**
 * Three bands, because the two inputs to combined_confidence fail in
 * different ways: an alias or fuzzy-reference hit caps the score at 0.9,
 * while anything below 0.75 means the model itself was inferring.
 */
type ConfidenceBand = 'high' | 'medium' | 'low'

function confidenceBand(value: number): ConfidenceBand {
  if (value >= 0.9) return 'high'
  if (value >= 0.75) return 'medium'
  return 'low'
}

const BAND_LABEL: Record<ConfidenceBand, string> = {
  high: 'Near threshold',
  medium: 'Inferred',
  low: 'Uncertain',
}

/**
 * Percentage, colour and bar length all encode the same number, so the
 * reading never depends on colour alone.
 */
function ConfidenceMeter({
  value,
  showLabel = false,
}: {
  value: number
  showLabel?: boolean
}) {
  const band = confidenceBand(value)
  return (
    <div className={`conf-${band} flex flex-col items-end gap-1`}>
      <span className="flex items-center gap-2">
        {showLabel && (
          <span className="text-xs text-muted-foreground">
            {BAND_LABEL[band]}
          </span>
        )}
        <span
          className="rounded-md border px-2 py-0.5 text-xs font-semibold tabular-nums"
          style={{
            color: 'var(--conf-ink)',
            backgroundColor:
              'color-mix(in oklab, var(--conf-tint) 12%, transparent)',
            borderColor:
              'color-mix(in oklab, var(--conf-tint) 30%, transparent)',
          }}
        >
          {percent(value)}
        </span>
      </span>
      <span
        className="h-1 w-16 overflow-hidden rounded-full"
        style={{
          backgroundColor:
            'color-mix(in oklab, var(--conf-tint) 18%, transparent)',
        }}
        aria-hidden
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${Math.round(value * 100)}%`,
            backgroundColor: 'var(--conf-ink)',
          }}
        />
      </span>
    </div>
  )
}

/** A dot beside the field name — the name itself still carries the meaning. */
function FieldLabel({ field }: { field: string }) {
  return (
    <span className={`field-${field} flex items-center gap-2`}>
      <span
        className="size-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: 'var(--field-dot, var(--muted-foreground))' }}
        aria-hidden
      />
      <span className="font-mono text-xs">{field}</span>
    </span>
  )
}

// ── states ───────────────────────────────────────────────────────────────────

function ReviewSkeleton() {
  return (
    <main className="page-wrap py-8">
      <div className="mb-6 flex items-center justify-between">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-6 w-24" />
      </div>
      <Card>
        <CardContent className="p-0">
          <div className="divide-y divide-border">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 p-4">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-40 flex-1" />
                <Skeleton className="h-6 w-12" />
                <Skeleton className="h-8 w-20" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </main>
  )
}

function ReviewError({ error }: { error: Error }) {
  return (
    <main className="page-wrap py-16">
      <Card className="mx-auto max-w-lg">
        <CardHeader>
          <CardTitle>Could not load the review queue</CardTitle>
          <CardDescription>{error.message}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Check that the backend is running on port 8000:{' '}
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
              pnpm api
            </code>
          </p>
        </CardContent>
      </Card>
    </main>
  )
}

function EmptyState() {
  return (
    <main className="page-wrap py-16">
      <Card className="mx-auto max-w-lg">
        <CardHeader className="items-center text-center">
          <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-full bg-muted">
            <Inbox className="size-5 text-muted-foreground" />
          </div>
          <CardTitle>Nothing to review</CardTitle>
          <CardDescription>
            Every extracted change has either been applied automatically or
            already decided on.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-center text-sm text-muted-foreground">
          <p>
            Load the demo dataset with{' '}
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
              pnpm seed
            </code>
            , or send a supplier email from the compose page.
          </p>
        </CardContent>
      </Card>
    </main>
  )
}

// ── review dialog ────────────────────────────────────────────────────────────

function ReviewDialog({
  change,
  open,
  onOpenChange,
  onResolved,
}: {
  change: ProposedChange | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onResolved: (id: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState<RejectionReason | ''>('')

  if (!change) return null

  function close() {
    onOpenChange(false)
    setRejecting(false)
    setReason('')
  }

  async function handleApprove() {
    if (!change) return
    setBusy(true)
    try {
      const result = await api.approveChange(change.id)
      if (result.status === 'superseded') {
        toast.warning('Superseded', {
          description:
            'The record changed after this was proposed, so nothing was written.',
        })
      } else {
        toast.success('Applied', {
          description: `${change.product_sku} ${change.field_name} is now ${formatValue(change.new_value)}.`,
        })
      }
      onResolved(change.id)
      close()
    } catch (err) {
      toast.error('Could not apply', {
        description: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setBusy(false)
    }
  }

  async function handleReject() {
    if (!change || !reason) return
    setBusy(true)
    try {
      await api.rejectChange(change.id, reason)
      toast.success('Rejected', {
        description: 'Recorded against this supplier for pattern analysis.',
      })
      onResolved(change.id)
      close()
    } catch (err) {
      toast.error('Could not reject', {
        description: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
    >
      {/* sm:max-w-* is needed to beat the component's own sm:max-w-sm default */}
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            PO {change.po_reference} · {change.product_sku}
          </DialogTitle>
          <DialogDescription>
            Proposed from an email by {senderName(change.sender)}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 md:grid-cols-2">
          {/* proposed change */}
          <section className="space-y-4">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Proposed change
            </h3>

            <div className="rounded-lg border border-border p-4">
              <div className="mb-2 text-muted-foreground">
                <FieldLabel field={change.field_name} />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground line-through">
                  {formatValue(change.old_value)}
                </span>
                <ArrowRight className="size-3.5 text-muted-foreground" />
                <span className="font-semibold">
                  {formatValue(change.new_value)}
                </span>
              </div>
            </div>

            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-muted-foreground">Product</dt>
                <dd className="text-right">
                  {change.product_title}
                  <span className="block font-mono text-xs text-muted-foreground">
                    {change.product_sku}
                  </span>
                </dd>
              </div>
              <Separator />
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-muted-foreground">
                  Extraction confidence
                </dt>
                <dd className="tabular-nums">
                  {percent(change.extraction_confidence)}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-muted-foreground">
                  Match confidence
                </dt>
                <dd className="tabular-nums">
                  {percent(change.match_confidence)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4 pt-1 font-medium">
                <dt>Combined</dt>
                <dd>
                  <ConfidenceMeter
                    value={change.combined_confidence}
                    showLabel
                  />
                </dd>
              </div>
            </dl>

            {change.evidence_text && (
              <div>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Evidence
                </h4>
                <blockquote className="border-l-2 border-border pl-3 text-sm italic">
                  {change.evidence_text}
                </blockquote>
              </div>
            )}
          </section>

          {/* original email */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Original email
            </h3>
            <div className="rounded-lg border border-border">
              <div className="space-y-1 border-b border-border p-3 text-sm">
                <p className="font-medium">
                  {change.subject || '(no subject)'}
                </p>
                <p className="font-mono text-xs text-muted-foreground">
                  {change.sender}
                </p>
              </div>
              <pre className="max-h-72 overflow-auto p-3 font-sans text-sm leading-relaxed whitespace-pre-wrap">
                {change.body_text || '(no body)'}
              </pre>
            </div>
          </section>
        </div>

        {rejecting && (
          <div className="space-y-2 rounded-lg border border-border p-4">
            <Label htmlFor="rejection-reason">
              Reason for rejection <span className="text-destructive">*</span>
            </Label>
            <Select
              value={reason}
              onValueChange={(value) => setReason(value as RejectionReason)}
            >
              <SelectTrigger id="rejection-reason" className="w-full">
                <SelectValue placeholder="Select a reason" />
              </SelectTrigger>
              <SelectContent>
                {REJECTION_REASONS.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Reasons are aggregated per supplier on the monitoring page to show
              where extraction goes wrong.
            </p>
          </div>
        )}

        <DialogFooter>
          {rejecting ? (
            <>
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => setRejecting(false)}
              >
                Back
              </Button>
              <Button
                variant="destructive"
                disabled={busy || !reason}
                onClick={handleReject}
              >
                {busy && <Loader2 className="size-4 animate-spin" />}
                Confirm rejection
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => setRejecting(true)}
              >
                Reject
              </Button>
              <Button disabled={busy} onClick={handleApprove}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                Approve and apply
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── summary ──────────────────────────────────────────────────────────────────

/**
 * What the pipeline did with everything it has seen. The point of the strip
 * is the ratio: most changes never reach a human, and the ones that do are
 * here because a score fell short — not because something broke.
 *
 * Values use proportional figures (tabular-nums is for aligned columns, not
 * standalone display numbers), and each tile's identity is carried by a mark
 * beside the label rather than by colouring the number itself.
 */
function StatTile({
  label,
  value,
  tint,
  hint,
}: {
  label: string
  value: number | string
  tint: string
  hint: string
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-1 flex items-center gap-2">
        <span
          className="size-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: tint }}
          aria-hidden
        />
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
      </div>
      <p className="text-2xl font-semibold">{value}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
    </div>
  )
}

function SummaryStrip({
  summary,
}: {
  summary: MonitoringData['changes_summary']
}) {
  return (
    <div className="mb-6 grid gap-3 sm:grid-cols-3">
      <StatTile
        label="Awaiting review"
        value={summary.pending}
        tint="var(--conf-amber)"
        hint="below the auto-apply threshold"
      />
      <StatTile
        label="Applied"
        value={summary.total_applied}
        tint="var(--conf-green)"
        hint={
          summary.avg_confidence
            ? `${Math.round(Number(summary.avg_confidence) * 100)}% average confidence`
            : 'written back with an audit entry'
        }
      />
      <StatTile
        label="Rejected"
        value={summary.total_rejected}
        tint="var(--conf-red)"
        hint="reasons feed the monitoring page"
      />
    </div>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

function ReviewPage() {
  const { changes: initial, summary } = Route.useLoaderData()
  const router = useRouter()
  const [changes, setChanges] = useState<Array<ProposedChange>>(initial)
  const [selected, setSelected] = useState<ProposedChange | null>(null)
  const [open, setOpen] = useState(false)

  function resolve(id: string) {
    setChanges((prev) => prev.filter((c) => c.id !== id))
    void router.invalidate()
  }

  if (changes.length === 0) return <EmptyState />

  return (
    <main className="page-wrap py-8">
      <div className="mb-6">
        <h1 className="text-xl font-bold">Review queue</h1>
        <p className="text-sm text-muted-foreground">
          Changes below the auto-apply threshold, waiting on a decision.
        </p>
      </div>

      <SummaryStrip summary={summary} />

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>PO</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>Change</TableHead>
                <TableHead className="text-right">Confidence</TableHead>
                <TableHead className="w-px" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {changes.map((change) => (
                <TableRow
                  key={change.id}
                  className={`conf-${confidenceBand(change.combined_confidence)}`}
                >
                  <TableCell className="relative font-medium">
                    <span
                      className="absolute top-0 left-0 h-full w-[3px]"
                      style={{ backgroundColor: 'var(--conf-ink)' }}
                      aria-hidden
                    />
                    {change.po_reference}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {change.product_sku}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    <FieldLabel field={change.field_name} />
                  </TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground line-through">
                        {formatValue(change.old_value)}
                      </span>
                      <ArrowRight className="size-3.5 text-muted-foreground" />
                      <span className="font-semibold text-foreground">
                        {formatValue(change.new_value)}
                      </span>
                    </span>
                  </TableCell>
                  <TableCell>
                    <ConfidenceMeter value={change.combined_confidence} />
                  </TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setSelected(change)
                        setOpen(true)
                      }}
                    >
                      Review
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <ReviewDialog
        change={selected}
        open={open}
        onOpenChange={setOpen}
        onResolved={resolve}
      />
    </main>
  )
}
