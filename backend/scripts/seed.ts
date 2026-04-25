#!/usr/bin/env tsx
/**
 * Seed canonical tables from db.xlsx.
 *
 * Reads: backend/data/db.xlsx  (or XLSX_PATH env var)
 * Truncates and reseeds: product, supplier, purchase_order,
 *   purchase_order_line, supplier_product, supplier_email_aliases
 *
 * Safe to run multiple times in development.
 */

import pg from 'pg'
import XLSX from 'xlsx'

const { Pool } = pg
const pool = new Pool({ connectionString: process.env.DATABASE_URL })

// ── Excel date conversion ────────────────────────────────────────────────────
// Excel stores dates as days since 1899-12-30 (the "1900 date system").
const EXCEL_EPOCH_MS = new Date(Date.UTC(1899, 11, 30)).getTime()

function excelDateToISO(val: unknown): string | null {
  if (val === null || val === undefined || val === '') return null
  if (typeof val !== 'number') return null
  return new Date(EXCEL_EPOCH_MS + val * 86_400_000).toISOString().slice(0, 10)
}

function nullify(val: unknown): unknown {
  if (val === null || val === undefined || val === '' || val === '-') return null
  return val
}

// ── main ─────────────────────────────────────────────────────────────────────

async function seed() {
  const xlsxPath = process.env.XLSX_PATH ?? './backend/data/db.xlsx'
  const wb = XLSX.readFile(xlsxPath)

  const read = (sheet: string) =>
    XLSX.utils.sheet_to_json<Record<string, unknown>>(wb.Sheets[sheet], {
      defval: null,
    })

  const productRows   = read('product')
  const supplierRows  = read('supplier')
  const poRows        = read('purchase_order')
  const polRows       = read('purchase_order_line')
  const spRows        = read('supplier_product')

  console.log(
    `xlsx loaded: ${productRows.length} products, ${supplierRows.length} suppliers,` +
    ` ${poRows.length} POs, ${polRows.length} lines, ${spRows.length} supplier_products`,
  )

  const db = await pool.connect()
  try {
    await db.query('BEGIN')

    // Truncate in reverse FK dependency order; CASCADE handles child rows.
    await db.query(
      `TRUNCATE supplier_email_aliases, supplier_product,
                purchase_order_line, purchase_order,
                product, supplier
       RESTART IDENTITY CASCADE`,
    )

    // Legacy-id → new uuid maps for FK resolution
    const productMap  = new Map<number, string>()
    const supplierMap = new Map<number, string>()
    const poMap       = new Map<number, string>()

    // ── product ──────────────────────────────────────────────────────────────
    for (const r of productRows) {
      const { rows } = await db.query(
        `INSERT INTO product (legacy_id, sku, title) VALUES ($1, $2, $3) RETURNING id`,
        [r.id, r.sku, nullify(r.title)],
      )
      productMap.set(r.id as number, rows[0].id)
    }
    console.log(`  ✓ ${productRows.length} products`)

    // ── supplier + one alias per supplier ────────────────────────────────────
    for (const r of supplierRows) {
      const { rows } = await db.query(
        `INSERT INTO supplier (legacy_id, name, email) VALUES ($1, $2, $3) RETURNING id`,
        [r.id, r.name, r.email],
      )
      const newId = rows[0].id as string
      supplierMap.set(r.id as number, newId)

      // Seed the primary email as an alias so alias-based resolution works too.
      await db.query(
        `INSERT INTO supplier_email_aliases (supplier_id, email_address) VALUES ($1, $2)`,
        [newId, r.email],
      )
    }
    console.log(`  ✓ ${supplierRows.length} suppliers (+ ${supplierRows.length} email aliases)`)

    // ── purchase_order ───────────────────────────────────────────────────────
    for (const r of poRows) {
      const supplierId = supplierMap.get(r.supplier_id as number)
      if (!supplierId) throw new Error(`Unknown supplier legacy_id: ${r.supplier_id}`)
      const { rows } = await db.query(
        `INSERT INTO purchase_order (legacy_id, reference_num, supplier_id, delivery_date)
         VALUES ($1, $2, $3, $4) RETURNING id`,
        [r.id, r.reference_num, supplierId, excelDateToISO(r.delivery_date)],
      )
      poMap.set(r.id as number, rows[0].id)
    }
    console.log(`  ✓ ${poRows.length} purchase_orders`)

    // ── purchase_order_line (batch in chunks of 100) ─────────────────────────
    const polParams: unknown[][] = polRows.map((r) => {
      const poId      = poMap.get(r.purchase_order_id as number)
      const productId = productMap.get(r.product_id as number)
      if (!poId)      throw new Error(`Unknown PO legacy_id: ${r.purchase_order_id}`)
      if (!productId) throw new Error(`Unknown product legacy_id: ${r.product_id}`)
      return [r.id, poId, productId, r.quantity, excelDateToISO(r.delivery_date)]
    })

    const CHUNK = 100
    for (let i = 0; i < polParams.length; i += CHUNK) {
      const chunk = polParams.slice(i, i + CHUNK)
      const placeholders = chunk
        .map((_, j) => {
          const b = j * 5
          return `($${b + 1},$${b + 2},$${b + 3},$${b + 4},$${b + 5})`
        })
        .join(',')
      await db.query(
        `INSERT INTO purchase_order_line
           (legacy_id, purchase_order_id, product_id, quantity, delivery_date)
         VALUES ${placeholders}`,
        chunk.flat(),
      )
    }
    console.log(`  ✓ ${polRows.length} purchase_order_lines`)

    // ── supplier_product ─────────────────────────────────────────────────────
    for (const r of spRows) {
      const supplierId = supplierMap.get(r.supplier_id as number)
      const productId  = productMap.get(r.product_id as number)
      if (!supplierId || !productId)
        throw new Error(`Unknown ids in supplier_product row: ${JSON.stringify(r)}`)
      await db.query(
        `INSERT INTO supplier_product (supplier_id, product_id, supplier_sku, price_per_unit)
         VALUES ($1, $2, $3, $4)`,
        [supplierId, productId, nullify(r.supplier_sku), nullify(r.price_per_unit)],
      )
    }
    console.log(`  ✓ ${spRows.length} supplier_products`)

    await db.query('COMMIT')
    console.log('Seed complete.')
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {})
    throw err
  } finally {
    db.release()
    await pool.end()
  }
}

seed().catch((e) => {
  console.error(e)
  process.exit(1)
})
