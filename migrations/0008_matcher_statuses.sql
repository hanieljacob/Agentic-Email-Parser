-- =============================================================================
-- 0008_matcher_statuses.sql
-- Add 'matched' and 'needs_review' to the emails status domain.
-- =============================================================================

BEGIN;

ALTER TABLE emails DROP CONSTRAINT emails_status_check;
ALTER TABLE emails ADD CONSTRAINT emails_status_check CHECK (status IN (
  'ingested',      -- written to DB, not yet queued for extraction
  'pending',       -- queued for extraction
  'processing',    -- extraction in flight
  'extracted',     -- LLM ran; not yet matched
  'matched',       -- all PO refs and SKUs resolved; proposed_changes created
  'needs_review',  -- extraction ran but some refs could not be matched
  'failed',        -- all extraction attempts errored
  'skipped'        -- intentionally excluded (e.g. OOO)
));

COMMIT;
