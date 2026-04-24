-- =============================================================================
-- 0007_mvp_schema.sql
-- Adapt emails table for MVP: local attachment storage (no S3 yet),
-- body_text column for the extracted plain-text body, and 'ingested' status.
-- =============================================================================

BEGIN;

-- S3 fields are not used for MVP; local ./attachments/ directory is used instead.
ALTER TABLE emails
  ALTER COLUMN s3_bucket DROP NOT NULL,
  ALTER COLUMN s3_key    DROP NOT NULL;

-- Store extracted plain-text body directly on the row.
ALTER TABLE emails ADD COLUMN body_text text;

-- Add 'ingested' as the initial status set by the ingestion service,
-- distinct from 'pending' (queued for extraction).
ALTER TABLE emails DROP CONSTRAINT emails_status_check;
ALTER TABLE emails ADD CONSTRAINT emails_status_check CHECK (status IN (
  'ingested',   -- written to DB, not yet queued for extraction
  'pending',    -- queued for extraction
  'processing', -- extraction in flight
  'extracted',  -- at least one successful run
  'failed',     -- all extraction attempts errored
  'skipped'     -- intentionally excluded (e.g. OOO)
));

COMMIT;
