# Migrations

Postgres 15+. Apply in order. Each file is transaction-wrapped; a failure rolls back the whole file.

```
psql "$DATABASE_URL" \
  -f 0001_canonical.sql \
  -f 0002_supplier_intelligence.sql \
  -f 0003_emails.sql \
  -f 0004_extraction.sql \
  -f 0005_staging.sql \
  -f 0006_audit.sql
```

## File order

| File | Tables |
|---|---|
| `0001_canonical.sql` | `suppliers`, `products`, `purchase_orders`, `purchase_order_lines` |
| `0002_supplier_intelligence.sql` | `supplier_email_aliases`, `product_aliases` |
| `0003_emails.sql` | `emails` |
| `0004_extraction.sql` | `extraction_runs` |
| `0005_staging.sql` | `proposed_changes` |
| `0006_audit.sql` | `audit_log` |

---

## Staging-to-canonical flow

```
Email arrives
  │
  ▼
emails                        idempotent on content_hash
  │
  ▼
extraction_runs               one row per LLM call; raw output in llm_output
  │
  ▼
proposed_changes              one row per field-level proposed update
  │
  ├── combined_confidence ≥ threshold ──► apply (applied_by = 'auto')
  │
  └── combined_confidence < threshold ──► human review
                                              │
                                    approved ─┤
                                    rejected ─┘ (no write)
                                              │
                                              ▼
                                    UPDATE canonical table
                                    INSERT audit_log row
                                    UPDATE proposed_changes SET status = 'applied'
```

Nothing writes to `purchase_orders` or `purchase_order_lines` except through this path. Every canonical write has a corresponding `audit_log` row → `proposed_changes` row → `extraction_runs` row → `emails` row.

---

## Version-based concurrency check

Every canonical table has `version integer NOT NULL DEFAULT 1`, incremented by trigger on each `UPDATE`.

When the extractor creates a `proposed_changes` row it records `target_record_version` = the canonical record's version at that moment.

At writeback time the application must:

```sql
UPDATE purchase_order_lines          -- or purchase_orders
SET    expected_delivery_date = $new_value
WHERE  id      = $target_record_id
  AND  version = $target_record_version;   -- ← optimistic lock

-- Check rows_affected = 1.
-- rows_affected = 0 means the record was modified since extraction:
--   another proposed_change was applied first, or the ERP sync ran.
-- In that case: mark this proposal rejected or re-queue for re-review
-- with the current canonical state.
```

This prevents last-write-wins corruption without requiring serialisable isolation across the whole pipeline.

---

## Adding multi-tenancy later

When ready, add `tenant_id uuid NOT NULL REFERENCES tenants(id)` to every table in a single migration. No existing column names conflict. Row-level security policies key on `tenant_id`. The design is intentionally flat so this is an additive change.
