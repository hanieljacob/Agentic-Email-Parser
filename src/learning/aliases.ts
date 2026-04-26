/**
 * Alias learning: two correction paths called from the review UI.
 *
 * assignSupplier — links an email sender address to a known supplier.
 * correctSku     — teaches the system a supplier's own SKU for a product.
 */

import pg from 'pg'
import { pool } from '#/db.js'
import { parseSenderEmail } from '#/utils/email.js'

// Lazy import to avoid circular init at module load time.
const getExtract = () => import('../../backend/extract.js').then((m) => m.extract)
const getMatch   = () => import('../../backend/match.js').then((m) => m.match)

// ── shared helper ─────────────────────────────────────────────────────────────

async function resolveSupplierId(db: pg.PoolClient, senderRaw: string): Promise<string | null> {
  const email = parseSenderEmail(senderRaw)
  const res = await db.query<{ id: string }>(
    `SELECT s.id FROM supplier s WHERE lower(s.email) = $1
     UNION
     SELECT s.id FROM supplier s
     JOIN supplier_email_aliases sea ON sea.supplier_id = s.id
     WHERE lower(sea.email_address) = $1
     LIMIT 1`,
    [email],
  )
  return res.rows[0]?.id ?? null
}

// ── assignSupplier ────────────────────────────────────────────────────────────

export interface AssignSupplierResult {
  ok: boolean
  aliasInserted: boolean
  retriggered: boolean
}

export async function assignSupplier(
  emailId: string,
  supplierId: string,
  retrigger = true,
): Promise<AssignSupplierResult> {
  const emailRes = await pool.query<{ sender: string }>(
    `SELECT sender FROM emails WHERE id = $1`,
    [emailId],
  )
  const sender = emailRes.rows[0]?.sender
  if (!sender) throw new Error(`email not found: ${emailId}`)

  const emailAddress = parseSenderEmail(sender)

  const insertRes = await pool.query(
    `INSERT INTO supplier_email_aliases (supplier_id, email_address)
     VALUES ($1, $2)
     ON CONFLICT DO NOTHING`,
    [supplierId, emailAddress],
  )
  const aliasInserted = (insertRes.rowCount ?? 0) > 0

  let retriggered = false
  if (retrigger) {
    try {
      const extract = await getExtract()
      const match   = await getMatch()
      const runId = await extract(emailId)
      await match(runId)
      retriggered = true
    } catch (err) {
      // Re-trigger is best-effort; don't fail the alias insert over it.
      console.error('retrigger failed:', err)
    }
  }

  return { ok: true, aliasInserted, retriggered }
}

// ── correctSku ────────────────────────────────────────────────────────────────

export interface CorrectSkuResult {
  ok: boolean
  supplier_sku: string | null
  line_updated: boolean
}

export async function correctSku(
  proposedChangeId: string,
  correctProductId: string,
): Promise<CorrectSkuResult> {
  const db = await pool.connect()
  try {
    await db.query('BEGIN')

    // Load the proposed_change with its extraction run and email sender
    const pcRes = await db.query(
      `SELECT pc.*, er.llm_output, e.sender
       FROM proposed_changes pc
       JOIN extraction_runs er ON er.id = pc.extraction_run_id
       JOIN emails e           ON e.id  = pc.email_id
       WHERE pc.id = $1`,
      [proposedChangeId],
    )
    const pc = pcRes.rows[0]
    if (!pc) throw new Error(`proposed_change not found: ${proposedChangeId}`)

    const supplierId = await resolveSupplierId(db, pc.sender as string)
    if (!supplierId) throw new Error('cannot resolve supplier for this email')

    // Find the sku_or_code the LLM used — scan llm_output for the matching line_update.
    // We match on evidence_text + field_name since those are stored on the proposed_change.
    type LLMOutput = { po_updates: Array<{ line_updates: Array<{ sku_or_code: string; field: string; evidence: string }> }> }
    const llm = pc.llm_output as LLMOutput
    let foundSku: string | null = null
    outer: for (const po of llm.po_updates ?? []) {
      for (const lu of po.line_updates ?? []) {
        if (lu.evidence === pc.evidence_text && lu.field === pc.field_name) {
          foundSku = lu.sku_or_code
          break outer
        }
      }
    }

    // Look up the correct product's canonical SKU for the correction record.
    const correctProductRes = await db.query<{ sku: string }>(
      `SELECT sku FROM product WHERE id = $1`,
      [correctProductId],
    )
    const correctProductSku = correctProductRes.rows[0]?.sku ?? null

    // Upsert the learned mapping into supplier_product
    if (foundSku) {
      await db.query(
        `INSERT INTO supplier_product (supplier_id, product_id, supplier_sku)
         VALUES ($1, $2, $3)
         ON CONFLICT (supplier_id, product_id)
         DO UPDATE SET supplier_sku = EXCLUDED.supplier_sku`,
        [supplierId, correctProductId, foundSku],
      )

      // Record a few-shot correction example for future extractions.
      if (pc.evidence_text && correctProductSku) {
        await db.query(
          `INSERT INTO supplier_corrections (supplier_id, context, wrong, correct, field)
           VALUES ($1, $2, $3, $4, 'sku_or_code')`,
          [supplierId, pc.evidence_text, foundSku, correctProductSku],
        )
      }
    }

    // Re-point the proposed_change at the correct purchase_order_line on the same PO.
    const currentLineRes = await db.query(
      `SELECT purchase_order_id FROM purchase_order_line WHERE id = $1`,
      [pc.target_record_id],
    )
    const poId = currentLineRes.rows[0]?.purchase_order_id

    let lineUpdated = false
    if (poId) {
      const correctLineRes = await db.query(
        `SELECT id, version FROM purchase_order_line
         WHERE purchase_order_id = $1 AND product_id = $2
         LIMIT 1`,
        [poId, correctProductId],
      )
      const correctLine = correctLineRes.rows[0]
      if (correctLine) {
        await db.query(
          `UPDATE proposed_changes
           SET target_record_id = $1, target_record_version = $2
           WHERE id = $3`,
          [correctLine.id, correctLine.version, proposedChangeId],
        )
        lineUpdated = true
      }
    }

    await db.query('COMMIT')
    return { ok: true, supplier_sku: foundSku, line_updated: lineUpdated }
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {})
    throw err
  } finally {
    db.release()
  }
}
