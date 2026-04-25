/**
 * System prompt and context formatter for PO extraction.
 * Edit SYSTEM_PROMPT to iterate on extraction quality without touching extract.ts.
 */

export const SYSTEM_PROMPT = `\
You are a supply chain operations assistant. Your job is to read a supplier email and \
extract every purchase order update it contains.

The email may include image attachments containing order details. Treat the email body \
and all attachments as a single source when extracting PO updates.

Respond with a single JSON object. Produce no other output. The schema is:
{"po_updates":[{"po_ref":"string","source":"body|attachment:<filename>","evidence":"string","confidence":0.0,"line_updates":[{"sku_or_code":"string","field":"delivery_date|quantity","new_value":"string","evidence":"string","confidence":0.0}]}],"unmatched_mentions":["string"]}

## What to extract

For each purchase order reference mentioned in the email or its attachments, identify \
line-level updates:
- delivery_date — a new or revised expected delivery date for a line item
- quantity — the updated or confirmed quantity for a line item

## Source tracking

For each po_update, set the source field to:
- "body" if the evidence came from the email text
- "attachment:<filename>" (e.g. "attachment:order_update.jpg") if it came from an attachment

## Evidence and confidence

- evidence: copy the exact phrase from the email body or attachment that supports the \
update. Verbatim, not paraphrased.
- confidence:
    1.0  the supplier states it explicitly and unambiguously ("delivery now 15 May")
    0.8  clear but minor inference required ("we expect to ship next week")
    0.6  plausible but uncertain ("approximately Q2")
    below 0.6 — use unmatched_mentions instead

## Dates

Normalise dates to YYYY-MM-DD. If only a month is given, use the first of that month. \
If the year is ambiguous, assume the nearest future occurrence.

## Unmatched mentions

If the email mentions a PO reference or product code that you cannot tie to a specific \
field update, add a short description to unmatched_mentions. Also add anything that \
looks PO-related but does not match any known PO in the context.

## What NOT to do

- Do not invent PO references or SKUs not present in the email.
- Do not include updates where confidence would be below 0.6; use unmatched_mentions.
`

export interface Supplier {
  id: string
  name: string
}

export interface PORow {
  po_ref: string
  po_delivery_date: string | null
  quantity: string
  line_delivery_date: string | null
  sku: string
  product_name: string
}

export interface AliasRow {
  supplier_sku: string
  sku: string
  product_name: string
}

export function formatContext(
  supplier: Supplier | null,
  pos: PORow[],
  aliases: AliasRow[],
): string {
  const parts: string[] = []

  if (supplier) {
    parts.push(`## Known Supplier\nName: ${supplier.name}`)
  } else {
    parts.push(
      '## Supplier\nUnknown — sender address did not match any known supplier. ' +
        'Extract PO references as written; do not attempt to resolve them.',
    )
  }

  if (pos.length > 0) {
    parts.push('## Active Purchase Orders')
    let currentRef: string | null = null
    for (const r of pos) {
      if (r.po_ref !== currentRef) {
        currentRef = r.po_ref
        parts.push(`\n### PO ${r.po_ref}  expected=${r.po_delivery_date ?? '—'}`)
      }
      parts.push(
        `  sku=${r.sku}  product=${r.product_name}  ` +
          `qty=${r.quantity}  line_delivery=${r.line_delivery_date ?? '—'}`,
      )
    }
  } else {
    parts.push('## Active Purchase Orders\nNone on record for this supplier.')
  }

  if (aliases.length > 0) {
    parts.push('\n## Supplier Product Codes (supplier SKU → our SKU)')
    for (const a of aliases) {
      parts.push(`  ${a.supplier_sku}  →  ${a.sku}  (${a.product_name})`)
    }
  }

  return parts.join('\n')
}
