-- =============================================================================
-- 0013_monitoring.sql
-- Read-only views for operational monitoring.
-- =============================================================================

BEGIN;

-- Quick per-status email count — primary pipeline health indicator.
CREATE VIEW pipeline_status AS
SELECT
  status,
  count(*)::integer AS count,
  max(received_at)  AS most_recent_at
FROM emails
GROUP BY status;

-- Per-supplier rejection breakdown.
-- A supplier with many rejections and no llm_notes is the clearest signal
-- that a developer should set supplier.llm_notes.
CREATE VIEW rejection_patterns AS
SELECT
  s.id                              AS supplier_id,
  s.name                            AS supplier_name,
  s.llm_notes IS NOT NULL           AS has_notes,
  pc.rejection_reason,
  count(*)::integer                 AS count
FROM proposed_changes pc
JOIN purchase_order_line pol ON pol.id = pc.target_record_id
JOIN purchase_order      po  ON po.id  = pol.purchase_order_id
JOIN supplier            s   ON s.id   = po.supplier_id
WHERE pc.status            = 'rejected'
  AND pc.rejection_reason IS NOT NULL
GROUP BY s.id, s.name, s.llm_notes IS NOT NULL, pc.rejection_reason;

COMMIT;
