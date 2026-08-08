# Migrations

Postgres 15+. Apply in order — each file is transaction-wrapped, so a failure rolls back that whole file.

```bash
pnpm migrate                      # runs every file in order
psql "$DATABASE_URL" -f 0001_canonical.sql   # or one at a time
```

There is no migration runner and no schema-version table. The files are append-only and idempotent as a set: re-running them on a populated database will fail on the `CREATE TABLE`s, so the workflow is create-database-then-migrate. `pnpm setup` does both.

## File order

| File                             | What it does                                                                              |
| -------------------------------- | ----------------------------------------------------------------------------------------- |
| `0001_canonical.sql`             | First-cut canonical tables (plural) and the shared `increment_version()` trigger function |
| `0002_supplier_intelligence.sql` | `supplier_email_aliases`, `product_aliases`                                               |
| `0003_emails.sql`                | `emails`, keyed for idempotency on `content_hash`                                         |
| `0004_extraction.sql`            | `extraction_runs` — one row per LLM call, raw output kept verbatim                        |
| `0005_staging.sql`               | `proposed_changes` — the staging layer everything writes through                          |
| `0006_audit.sql`                 | `audit_log` plus the triggers that make it immutable                                      |
| `0007_mvp_schema.sql`            | Local attachment storage instead of S3; `body_text`; the `ingested` status                |
| `0008_matcher_statuses.sql`      | Adds `matched` and `needs_review` to the email status domain                              |
| `0009_schema_restructure.sql`    | **Replaces the plural canonical tables with singular ones** shaped to match `db.xlsx`     |
| `0010_superseded_status.sql`     | Adds `superseded` to `proposed_changes.status`                                            |
| `0011_email_attachments.sql`     | `email_attachments`                                                                       |
| `0012_feedback.sql`              | `rejection_reason`, `supplier.llm_notes`, `supplier_corrections`                          |
| `0013_monitoring.sql`            | The `pipeline_status` and `rejection_patterns` views                                      |

> **Reading these out of order will mislead you.** `0001`–`0006` describe tables named `suppliers`, `products`, `purchase_orders` and `purchase_order_lines` that **no longer exist** — `0009` drops all four and replaces them with the singular `supplier`, `product`, `purchase_order` and `purchase_order_line` used everywhere in the application. The plural files are kept only so the history applies cleanly from an empty database.

## Current schema

**Canonical** — the source of truth for matching. Seeded from `backend/data/db.xlsx`; the pipeline never writes here except through the apply path.

| Table                 | Notable columns                                                           |
| --------------------- | ------------------------------------------------------------------------- |
| `product`             | `sku` (unique), `title`, `version`                                        |
| `supplier`            | `name`, `email` (unique), `llm_notes`, `version`                          |
| `purchase_order`      | `reference_num` (unique), `supplier_id`, `delivery_date`, `version`       |
| `purchase_order_line` | `purchase_order_id`, `product_id`, `quantity`, `delivery_date`, `version` |
| `supplier_product`    | PK `(supplier_id, product_id)`, `supplier_sku`, `price_per_unit`          |

Every canonical table carries `version integer NOT NULL DEFAULT 1` and a `BEFORE UPDATE` trigger running `increment_version()`, which bumps `version` and stamps `updated_at`.

**Pipeline** — `emails` → `extraction_runs` → `proposed_changes` → `audit_log`, plus `email_attachments`, `supplier_email_aliases` and `supplier_corrections`.

## Staging-to-canonical flow

```
email arrives
  │
  ▼
emails                  idempotent on content_hash
  │
  ▼
extraction_runs         one row per LLM call; raw output in llm_output
  │
  ▼
proposed_changes        one row per field-level proposed update
  │
  ├── combined_confidence >= threshold ──► apply (applied_by = 'auto')
  │
  └── combined_confidence <  threshold ──► human review
                                              │
                                    approved ─┤
                                    rejected ─┘ (no write)
                                              │
                                              ▼
                                    INSERT audit_log
                                    UPDATE purchase_order_line
                                    UPDATE proposed_changes SET status = 'applied'
```

Nothing writes to `purchase_order_line` except through this path, so every canonical value traces back: `audit_log` → `proposed_changes` → `extraction_runs` → `emails`.

## Version-based concurrency check

When the matcher stages a `proposed_changes` row it records `target_record_version` — the canonical row's `version` at that moment. The apply path in `backend/writeback.py` then, inside one transaction:

```sql
SELECT * FROM proposed_changes WHERE id = $1 FOR UPDATE;
SELECT version FROM purchase_order_line WHERE id = $2 FOR UPDATE;
-- if version <> target_record_version:
--     UPDATE proposed_changes SET status = 'superseded'; COMMIT; stop.
INSERT INTO audit_log (...);
UPDATE purchase_order_line SET <field> = $3 WHERE id = $2;   -- trigger bumps version
UPDATE proposed_changes SET status = 'applied' WHERE id = $1;
```

Both rows are taken `FOR UPDATE`, so concurrent applies queue rather than race, and the version comparison happens while the row is locked. A mismatch means the record moved on since extraction — another proposal was applied first, or an ERP resync ran — and nothing is written.

This prevents last-write-wins corruption without needing serialisable isolation across the whole pipeline. `backend/tests/test_writeback.py` exercises it, including two proposals racing on one row via `asyncio.gather` and an external session holding the lock.

## Adding multi-tenancy later

Add `tenant_id uuid NOT NULL REFERENCES tenants(id)` to every table in a single migration; no existing column names conflict, and row-level security policies key on it. The design is intentionally flat so this stays additive.
