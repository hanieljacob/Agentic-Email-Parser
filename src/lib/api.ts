/**
 * Typed client for the FastAPI backend.
 *
 * The frontend holds no database connection and writes no SQL — every read
 * and write goes through these calls. Types mirror the Pydantic response
 * models in backend/routers/.
 */

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export interface ProposedChange {
  id: string
  field_name: string
  old_value: string | null
  new_value: string
  evidence_text: string | null
  extraction_confidence: number
  match_confidence: number
  combined_confidence: number
  status: string
  created_at: string
  email_id: string
  sender: string
  subject: string | null
  body_text: string | null
  po_reference: string
  product_sku: string
  product_title: string | null
}

export interface ApplyResult {
  status: 'applied' | 'superseded'
}

export interface PipelineResult {
  run_id: string
  pending: number
  proposed: number
  auto_applied: number
  unmatched_pos: Array<string>
  unmatched_skus: Array<{ po_ref: string; sku_or_code: string }>
}

export interface IngestResult {
  email_id: string
  is_new: boolean
}

export interface MonitoringData {
  status_counts: Array<{ status: string; count: number }>
  stuck_emails: Array<{
    id: string
    sender: string
    subject: string | null
    status: string
    received_at: string
    attempt_count: number
  }>
  rejections: Array<{
    supplier_name: string
    has_notes: boolean
    rejection_reason: string
    count: number
  }>
  changes_summary: {
    pending: number
    total_applied: number
    total_rejected: number
    avg_confidence: string | null
  }
}

export const REJECTION_REASONS = [
  { value: 'wrong_date_format', label: 'Wrong date format (MM/DD vs DD/MM)' },
  { value: 'wrong_sku', label: 'Wrong SKU / product' },
  { value: 'not_a_po_update', label: 'Not a real PO update' },
  { value: 'quantity_is_delta', label: 'Quantity is a delta, not a total' },
  { value: 'wrong_po_reference', label: 'Wrong PO reference' },
  { value: 'llm_hallucination', label: 'Hallucination / invented data' },
  { value: 'other', label: 'Other' },
] as const

export type RejectionReason = (typeof REJECTION_REASONS)[number]['value']

/** Surfaces FastAPI's `detail` field so the UI can show a real message. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Response had no JSON body; the status line is the best we have.
    }
    throw new ApiError(detail, response.status)
  }

  return response.json() as Promise<T>
}

export const api = {
  listProposedChanges: (status = 'pending') =>
    request<Array<ProposedChange>>(
      `/proposed-changes?status=${encodeURIComponent(status)}`,
    ),

  approveChange: (id: string, reviewedBy = 'reviewer') =>
    request<ApplyResult>(`/proposed-changes/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ reviewed_by: reviewedBy }),
    }),

  rejectChange: (id: string, reason: RejectionReason, notes?: string) =>
    request<{ ok: boolean }>(`/proposed-changes/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        rejection_reason: reason,
        review_notes: notes ?? null,
      }),
    }),

  ingestEmail: (mime: string) =>
    request<IngestResult>(`/emails?no_pipeline=true`, {
      method: 'POST',
      body: mime,
      headers: { 'Content-Type': 'message/rfc822' },
    }),

  runPipeline: (emailId: string) =>
    request<PipelineResult>(`/emails/${emailId}/pipeline`, { method: 'POST' }),

  getMonitoring: () => request<MonitoringData>('/monitoring'),
}
