#!/usr/bin/env tsx
/**
 * Matching step: extraction_run_id → proposed_changes rows.
 *
 * Resolves po_ref and sku_or_code from LLM output to canonical DB records,
 * then inserts proposed_changes for each resolved line update.
 *
 * Entry point:
 *   CLI: tsx backend/match.ts <extraction_run_uuid>
 */

import pg from 'pg'

const { Pool } = pg
const pool = new Pool({ connectionString: process.env.DATABASE_URL })

// ── types (mirrors the extraction schema) ────────────────────────────────────

interface LineUpdate {
  sku_or_code: string
  field: 'delivery_date' | 'quantity'
  new_value: string
  evidence: string
  confidence: number
}

interface POUpdate {
  po_ref: string
  evidence: string
  confidence: number
  line_updates: LineUpdate[]
}

interface LLMOutput {
  po_updates: POUpdate[]
  unmatched_mentions: string[]
}

interface MatchSummary {
  proposed: number
  unmatched_pos: string[]
  unmatched_skus: Array<{ po_ref: string; sku_or_code: string }>
}

// ── normalization ────────────────────────────────────────────────────────────

function normalizeRef(s: string): string {
  return (
    s
      .toUpperCase()
      // Strip common "PO" prefix when followed by a separator or digit
      // so "PO-12" ~ "12" ~ "PO12" but "POMADE" is left alone
      .replace(/^PO(?=[-_\s]|\d)/, '')
      .replace(/^[-_\s]+/, '')   // strip any leading separator left behind
      .replace(/[\s\-_]/g, '')   // strip remaining separators
      .replace(/0+(\d)/g, '$1')  // collapse leading zeros in digit runs
  )
}

// ── PO resolution ────────────────────────────────────────────────────────────

interface PORow { id: string; version: number; reference_num: string }

async function resolvePO(
  db: pg.PoolClient,
  supplierId: string,
  poRef: string,
): Promise<{ row: PORow; match_confidence: number } | null> {
  // 1. Exact match
  const exact = await db.query<PORow>(
    `SELECT id, version, reference_num
     FROM purchase_order
     WHERE supplier_id = $1 AND reference_num = $2`,
    [supplierId, poRef],
  )
  if (exact.rows.length) return { row: exact.rows[0], match_confidence: 1.0 }

  // 2. Normalized match — load all POs for supplier and compare in memory
  const all = await db.query<PORow>(
    `SELECT id, version, reference_num FROM purchase_order WHERE supplier_id = $1`,
    [supplierId],
  )
  const norm = normalizeRef(poRef)
  const hit = all.rows.find((r) => normalizeRef(r.reference_num) === norm)
  if (hit) return { row: hit, match_confidence: 0.9 }

  return null
}

// ── line resolution ──────────────────────────────────────────────────────────

interface LineRow {
  id: string
  version: number
  quantity: string
  delivery_date: Date | null
  sku: string
}

async function resolveLine(
  db: pg.PoolClient,
  poId: string,
  supplierId: string,
  skuOrCode: string,
): Promise<{ row: LineRow; match_confidence: number } | null> {
  // 1. Exact SKU match on lines of this PO
  const exact = await db.query<LineRow>(
    `SELECT pol.id, pol.version, pol.quantity, pol.delivery_date, p.sku
     FROM   purchase_order_line pol
     JOIN   product p ON p.id = pol.product_id
     WHERE  pol.purchase_order_id = $1 AND p.sku = $2
     LIMIT  1`,
    [poId, skuOrCode],
  )
  if (exact.rows.length) return { row: exact.rows[0], match_confidence: 1.0 }

  // 2. Supplier SKU alias match via supplier_product
  const alias = await db.query<LineRow>(
    `SELECT pol.id, pol.version, pol.quantity, pol.delivery_date, p.sku
     FROM   purchase_order_line pol
     JOIN   product p            ON p.id = pol.product_id
     JOIN   supplier_product sp  ON sp.product_id = p.id
     WHERE  pol.purchase_order_id = $1
       AND  sp.supplier_id = $2
       AND  sp.supplier_sku = $3
     LIMIT  1`,
    [poId, supplierId, skuOrCode],
  )
  if (alias.rows.length) return { row: alias.rows[0], match_confidence: 0.9 }

  return null
}

// ── old value snapshot ───────────────────────────────────────────────────────

function getOldValue(line: LineRow, field: string): string | null {
  switch (field) {
    case 'delivery_date':
      return line.delivery_date ? line.delivery_date.toISOString().slice(0, 10) : null
    case 'quantity':
      return String(line.quantity)
    default:
      return null
  }
}

// ── core match function ──────────────────────────────────────────────────────

export async function match(runId: string): Promise<MatchSummary> {
  const db = await pool.connect()
  try {
    const runRes = await db.query<{ email_id: string; llm_output: LLMOutput }>(
      `SELECT email_id, llm_output FROM extraction_runs WHERE id = $1`,
      [runId],
    )
    if (!runRes.rows.length) throw new Error(`extraction_run not found: ${runId}`)
    const { email_id: emailId, llm_output: llmOutput } = runRes.rows[0]

    // Resolve supplier from email sender
    const emailRes = await db.query<{ sender: string }>(
      `SELECT sender FROM emails WHERE id = $1`,
      [emailId],
    )
    const sender = emailRes.rows[0]?.sender ?? ''
    const addrMatch = sender.match(/<([^>]+)>/)
    const senderEmail = (addrMatch ? addrMatch[1] : sender).toLowerCase()

    const supplierRes = await db.query<{ id: string }>(
      `SELECT s.id FROM supplier s WHERE lower(s.email) = $1
       UNION
       SELECT s.id FROM supplier s
       JOIN supplier_email_aliases sea ON sea.supplier_id = s.id
       WHERE lower(sea.email_address) = $1
       LIMIT 1`,
      [senderEmail],
    )
    const supplierId = supplierRes.rows[0]?.id ?? null

    const summary: MatchSummary = { proposed: 0, unmatched_pos: [], unmatched_skus: [] }

    await db.query('BEGIN')

    for (const poUpdate of llmOutput.po_updates ?? []) {
      if (!supplierId) {
        summary.unmatched_pos.push(poUpdate.po_ref)
        continue
      }

      const poMatch = await resolvePO(db, supplierId, poUpdate.po_ref)
      if (!poMatch) {
        summary.unmatched_pos.push(poUpdate.po_ref)
        continue
      }

      for (const lu of poUpdate.line_updates) {
        const lineMatch = await resolveLine(db, poMatch.row.id, supplierId, lu.sku_or_code)
        if (!lineMatch) {
          summary.unmatched_skus.push({ po_ref: poUpdate.po_ref, sku_or_code: lu.sku_or_code })
          continue
        }

        const extractionConfidence = lu.confidence
        const matchConfidence      = poMatch.match_confidence * lineMatch.match_confidence
        const combinedConfidence   = extractionConfidence * matchConfidence

        await db.query(
          `INSERT INTO proposed_changes
             (email_id, extraction_run_id,
              target_table, target_record_id, target_record_version,
              field_name, old_value, new_value,
              evidence_text,
              extraction_confidence, match_confidence, combined_confidence,
              status)
           VALUES ($1,$2,'purchase_order_line',$3,$4,$5,$6,$7,$8,$9,$10,$11,'pending')`,
          [
            emailId, runId,
            lineMatch.row.id, lineMatch.row.version,
            lu.field,
            getOldValue(lineMatch.row, lu.field),
            lu.new_value,
            lu.evidence,
            extractionConfidence, matchConfidence, combinedConfidence,
          ],
        )
        summary.proposed++
      }
    }

    const anyUnmatched =
      summary.unmatched_pos.length > 0 || summary.unmatched_skus.length > 0
    await db.query(
      `UPDATE emails SET status = $1 WHERE id = $2`,
      [anyUnmatched ? 'needs_review' : 'matched', emailId],
    )

    await db.query('COMMIT')
    return summary
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {})
    throw err
  } finally {
    db.release()
  }
}

// ── CLI entrypoint ───────────────────────────────────────────────────────────

import { fileURLToPath } from 'url'
if (fileURLToPath(import.meta.url) === process.argv[1]) {
  const arg = process.argv.slice(2).find((a) => a !== '--')
  if (!arg) {
    console.error('usage: tsx backend/match.ts <extraction_run_uuid>')
    process.exit(1)
  }

  match(arg)
    .then((s) => {
      console.log(`proposed: ${s.proposed}`)
      if (s.unmatched_pos.length)
        console.log(`unmatched POs: ${s.unmatched_pos.join(', ')}`)
      if (s.unmatched_skus.length)
        console.log(
          `unmatched SKUs: ${s.unmatched_skus.map((u) => `${u.po_ref}/${u.sku_or_code}`).join(', ')}`,
        )
    })
    .catch((e) => {
      console.error(e)
      process.exit(1)
    })
}
