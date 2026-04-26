/**
 * Writeback: apply an approved proposed_change to the canonical record.
 * Entire operation runs in a single transaction.
 */

import { pool } from '#/db.js'

// Whitelist prevents dynamic SQL injection via field_name values from the DB.
const WRITABLE_FIELDS: Record<string, string> = {
  delivery_date: 'delivery_date',
  quantity:      'quantity',
}

export interface ApplyResult {
  status: 'applied' | 'superseded'
}

export async function applyProposedChange(
  id: string,
  appliedBy = 'api',
): Promise<ApplyResult> {
  const db = await pool.connect()
  try {
    await db.query('BEGIN')

    // 1. Load proposed_change — row-lock so concurrent apply calls queue up.
    const pcRes = await db.query(
      `SELECT * FROM proposed_changes WHERE id = $1 FOR UPDATE`,
      [id],
    )
    const pc = pcRes.rows[0]
    if (!pc) throw new Error(`proposed_change not found: ${id}`)
    if (pc.status !== 'approved')
      throw new Error(`cannot apply: status is '${pc.status}', expected 'approved'`)

    if (pc.target_table !== 'purchase_order_line')
      throw new Error(`unsupported target_table: '${pc.target_table}'`)

    const col = WRITABLE_FIELDS[pc.field_name as string]
    if (!col) throw new Error(`unsupported field_name: '${pc.field_name}'`)

    // 2. Load target record — row-lock, then version check.
    const targetRes = await db.query(
      `SELECT version FROM purchase_order_line WHERE id = $1 FOR UPDATE`,
      [pc.target_record_id],
    )
    const target = targetRes.rows[0]
    if (!target) throw new Error(`target record not found: ${pc.target_record_id}`)

    if (target.version !== pc.target_record_version) {
      await db.query(
        `UPDATE proposed_changes SET status = 'superseded' WHERE id = $1`,
        [id],
      )
      await db.query('COMMIT')
      return { status: 'superseded' }
    }

    // 3. Audit log (immutable — insert only).
    await db.query(
      `INSERT INTO audit_log
         (target_table, target_record_id, field_name,
          prior_value, new_value, applied_by, proposed_change_id)
       VALUES ($1,$2,$3,$4,$5,$6,$7)`,
      [
        pc.target_table, pc.target_record_id, pc.field_name,
        pc.old_value, pc.new_value, appliedBy, id,
      ],
    )

    // 4. Write to canonical record. The increment_version trigger bumps version.
    await db.query(
      `UPDATE purchase_order_line SET ${col} = $1 WHERE id = $2`,
      [pc.new_value, pc.target_record_id],
    )

    // 5. Mark applied.
    await db.query(
      `UPDATE proposed_changes SET status = 'applied' WHERE id = $1`,
      [id],
    )

    await db.query('COMMIT')
    return { status: 'applied' }
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {})
    throw err
  } finally {
    db.release()
  }
}
