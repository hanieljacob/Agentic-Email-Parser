#!/usr/bin/env tsx
/**
 * Extraction step: email_id → context → LLM → extraction_runs row.
 *
 * Entry points:
 *   CLI:    tsx backend/extract.ts <email_uuid>
 *   Server: tsx backend/extract.ts --server
 */

import { createServer } from 'http'
import fs from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'

import OpenAI from 'openai'
import pg from 'pg'
import { z } from 'zod'
import { PDFParse } from 'pdf-parse'
import mammoth from 'mammoth'
import XLSX from 'xlsx'

import { SYSTEM_PROMPT, formatContext } from './prompt.js'

const { Pool } = pg
const pool = new Pool({ connectionString: process.env.DATABASE_URL })
const MODEL_NAME = process.env.MODEL_NAME ?? 'anthropic/claude-sonnet-4'

// ── Zod schema ──────────────────────────────────────────────────────────────

const LineUpdateSchema = z.object({
  sku_or_code: z.string(),
  field: z.enum(['delivery_date', 'quantity']),
  new_value: z.string(),
  evidence: z.string(),
  confidence: z.number().min(0).max(1),
})

const ExtractionSchema = z.object({
  po_updates: z
    .array(
      z.object({
        po_ref: z.string(),
        source: z.string().optional().default('body'),
        evidence: z.string(),
        confidence: z.number().min(0).max(1),
        line_updates: z.array(LineUpdateSchema).default([]),
      }),
    )
    .default([]),
  unmatched_mentions: z.array(z.string()).default([]),
})

function parseModelContent(content: string): unknown {
  const stripped = content.trim()
  // Strip markdown code fences if present
  const fenced = stripped.match(/```(?:json)?\s*([\s\S]*?)\s*```/)
  try {
    return JSON.parse(fenced ? fenced[1] : stripped)
  } catch {
    return {}
  }
}

// ── context loading ─────────────────────────────────────────────────────────

async function loadContext(senderRaw: string) {
  const match = senderRaw.match(/<([^>]+)>/)
  const senderEmail = (match ? match[1] : senderRaw).toLowerCase()

  // Check supplier.email first, then fall back to supplier_email_aliases
  const { rows: supplierRows } = await pool.query<{ id: string; name: string }>(
    `SELECT s.id, s.name FROM supplier s WHERE lower(s.email) = $1
     UNION
     SELECT s.id, s.name FROM supplier s
     JOIN supplier_email_aliases sea ON sea.supplier_id = s.id
     WHERE lower(sea.email_address) = $1
     LIMIT 1`,
    [senderEmail],
  )
  const supplier = supplierRows[0] ?? null
  if (!supplier) return { supplier: null, pos: [], aliases: [] }

  const { rows: poRows } = await pool.query(
    `SELECT po.reference_num,
            po.delivery_date     AS po_delivery_date,
            pol.quantity,
            pol.delivery_date    AS line_delivery_date,
            p.sku,
            p.title              AS product_name
     FROM   purchase_order po
     JOIN   purchase_order_line pol ON pol.purchase_order_id = po.id
     JOIN   product p               ON p.id = pol.product_id
     WHERE  po.supplier_id = $1
     ORDER  BY po.reference_num`,
    [supplier.id],
  )

  const { rows: aliasRows } = await pool.query(
    `SELECT sp.supplier_sku, p.sku, p.title AS product_name
     FROM   supplier_product sp
     JOIN   product p ON p.id = sp.product_id
     WHERE  sp.supplier_id = $1 AND sp.supplier_sku IS NOT NULL`,
    [supplier.id],
  )

  const pos = poRows.map((r) => ({
    po_ref:            r.reference_num,
    po_delivery_date:  r.po_delivery_date ? String(r.po_delivery_date) : null,
    quantity:          String(r.quantity),
    line_delivery_date: r.line_delivery_date ? String(r.line_delivery_date) : null,
    sku:               r.sku,
    product_name:      r.product_name,
  }))

  const aliases = aliasRows.map((r) => ({
    supplier_sku: r.supplier_sku,
    sku:          r.sku,
    product_name: r.product_name,
  }))

  return { supplier, pos, aliases }
}

// ── LLM client ──────────────────────────────────────────────────────────────

const client = new OpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: process.env.OPENROUTER_API_KEY,
})

// ── attachment loading ───────────────────────────────────────────────────────

const ATTACHMENTS_DIR = process.env.ATTACHMENTS_DIR
  ? path.resolve(process.env.ATTACHMENTS_DIR)
  : path.resolve('attachments')

interface ImageAttachment {
  originalName: string
  mimeType: string
  base64: string
}

interface TextAttachment {
  originalName: string
  mimeType: string
  text: string
}

// ── document text extraction ─────────────────────────────────────────────────

const XLSX_MIME_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-excel',
  'application/x-excel',
])

const DOCX_MIME_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/msword',
])

async function extractDocumentText(
  data: Buffer,
  mimeType: string,
  originalName: string,
): Promise<string | null> {
  try {
    if (mimeType === 'application/pdf') {
      const parser = new PDFParse({ data })
      const result = await parser.getText()
      const text = result.text.trim()
      if (text) return text
      // No text layer — likely a scanned image PDF
      return `[Scanned PDF — no text layer detected. A reviewer should inspect "${originalName}" directly.]`
    }

    if (XLSX_MIME_TYPES.has(mimeType)) {
      const wb = XLSX.read(data, { type: 'buffer' })
      return wb.SheetNames.map((name) => {
        const ws = wb.Sheets[name]
        return `[Sheet: ${name}]\n${XLSX.utils.sheet_to_csv(ws)}`
      }).join('\n\n')
    }

    if (DOCX_MIME_TYPES.has(mimeType)) {
      const result = await mammoth.extractRawText({ buffer: data })
      return result.value.trim() || null
    }

    if (mimeType === 'text/csv' || mimeType === 'text/plain') {
      return data.toString('utf-8').trim() || null
    }

    return null // unsupported format — silently skip
  } catch {
    return null
  }
}

// ── attachment loading ────────────────────────────────────────────────────────

interface AttachmentResult {
  images: ImageAttachment[]
  texts: TextAttachment[]
}

async function loadAttachments(emailId: string): Promise<AttachmentResult> {
  const { rows } = await pool.query<{
    stored_name: string
    original_name: string
    mime_type: string
  }>(
    `SELECT stored_name, original_name, mime_type
     FROM email_attachments
     WHERE email_id = $1`,
    [emailId],
  )

  const images: ImageAttachment[] = []
  const texts: TextAttachment[] = []

  for (const row of rows) {
    let data: Buffer
    try {
      data = await fs.readFile(path.join(ATTACHMENTS_DIR, row.stored_name))
    } catch {
      continue // skip unreadable files
    }

    if (row.mime_type.startsWith('image/')) {
      images.push({
        originalName: row.original_name,
        mimeType: row.mime_type,
        base64: data.toString('base64'),
      })
    } else {
      const text = await extractDocumentText(data, row.mime_type, row.original_name)
      if (text !== null) {
        texts.push({ originalName: row.original_name, mimeType: row.mime_type, text })
      }
    }
  }

  return { images, texts }
}

// ── core extraction ─────────────────────────────────────────────────────────

export async function extract(emailId: string): Promise<string> {
  const { rows } = await pool.query(
    'SELECT sender, subject, body_text FROM emails WHERE id = $1',
    [emailId],
  )
  if (!rows.length) throw new Error(`email not found: ${emailId}`)

  const { sender, subject, body_text: bodyText } = rows[0]
  const [{ supplier, pos, aliases }, { images: imageAttachments, texts: textAttachments }] =
    await Promise.all([loadContext(sender), loadAttachments(emailId)])

  // Base text: context + email body + all extractable document text
  const baseText = [
    formatContext(supplier, pos, aliases),
    `## Email\nFrom: ${sender}\nSubject: ${subject}\n\n${bodyText ?? '(no body)'}`,
    ...textAttachments.map((t) => `## Attachment: ${t.originalName}\n${t.text}`),
  ].join('\n\n')

  type ContentPart =
    | { type: 'text'; text: string }
    | { type: 'image_url'; image_url: { url: string } }

  const userContent: string | ContentPart[] =
    imageAttachments.length === 0
      ? baseText
      : [
          { type: 'text', text: baseText },
          ...imageAttachments.map((img): ContentPart => ({
            type: 'image_url',
            image_url: { url: `data:${img.mimeType};base64,${img.base64}` },
          })),
        ]

  let llmOutput: object = {}
  let status: 'success' | 'error' = 'success'
  let errorMessage: string | null = null

  const callLLM = async (content: string | ContentPart[]) => {
    try {
      const response = await client.chat.completions.create({
        model: MODEL_NAME,
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          { role: 'user', content: content as any },
        ],
      })
      return response.choices[0].message.content ?? ''
    } catch (err) {
      // Log full OpenRouter error body for debugging
      if (err instanceof OpenAI.APIError) {
        console.error(
          `[extract] OpenRouter error ${err.status} (model=${MODEL_NAME}):`,
          JSON.stringify(err.error ?? err.message),
        )
        throw new Error(`OpenRouter ${err.status}: ${JSON.stringify(err.error ?? err.message)}`)
      }
      throw err
    }
  }

  try {
    let raw: string
    try {
      raw = await callLLM(userContent)
    } catch (visionErr) {
      // Retry text-only if the model doesn't support vision (404 or 500)
      const msg = String(visionErr)
      if (Array.isArray(userContent) && (msg.includes('image') || msg.includes('500'))) {
        console.error('[extract] Vision call failed, retrying text-only:', msg)
        raw = await callLLM(baseText)
      } else {
        throw visionErr
      }
    }

    const parsed = ExtractionSchema.safeParse(parseModelContent(raw))
    if (!parsed.success) {
      throw new Error(`Schema validation failed: ${parsed.error.message}`)
    }
    llmOutput = parsed.data
  } catch (err) {
    status = 'error'
    errorMessage = String(err)
  }

  const db = await pool.connect()
  let runId = ''
  try {
    await db.query('BEGIN')

    const runResult = await db.query(
      `INSERT INTO extraction_runs (email_id, model_version, llm_output, status, error_message)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING id`,
      [emailId, MODEL_NAME, JSON.stringify(llmOutput), status, errorMessage],
    )
    runId = String(runResult.rows[0].id)

    await db.query('UPDATE emails SET status = $1 WHERE id = $2', [
      status === 'success' ? 'extracted' : 'failed',
      emailId,
    ])

    await db.query('COMMIT')
  } catch (err) {
    await db.query('ROLLBACK').catch(() => {})
    throw err
  } finally {
    db.release()
  }

  if (status === 'error') throw new Error(`Extraction failed: ${errorMessage}`)
  return runId
}

// ── HTTP server ─────────────────────────────────────────────────────────────

function startServer() {
  createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/extract') {
      res.writeHead(404).end()
      return
    }
    const body = await new Promise<string>((resolve) => {
      let data = ''
      req.on('data', (chunk) => { data += chunk })
      req.on('end', () => resolve(data))
    })
    try {
      const runId = await extract(body.trim())
      res.writeHead(200, { 'Content-Type': 'text/plain' }).end(runId)
    } catch (err) {
      res.writeHead(500).end(String(err))
    }
  }).listen(8001, () => console.log('extract server listening on :8001'))
}

// ── entrypoint ──────────────────────────────────────────────────────────────

if (fileURLToPath(import.meta.url) === process.argv[1]) {
  const arg = process.argv.slice(2).find((a) => a !== '--')
  if (arg === '--server') {
    startServer()
  } else if (arg) {
    extract(arg)
      .then(console.log)
      .catch((e) => { console.error(e); process.exit(1) })
  } else {
    console.error('usage: tsx backend/extract.ts <email_uuid> | --server')
    process.exit(1)
  }
}
