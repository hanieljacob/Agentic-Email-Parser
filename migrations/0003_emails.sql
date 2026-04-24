-- =============================================================================
-- 0003_emails.sql
-- One row per ingested email. Raw content lives in S3; only metadata and a
-- pointer are stored here. content_hash enforces idempotency: the same
-- physical email can never be inserted twice.
-- =============================================================================

BEGIN;

CREATE TABLE emails (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id    text        NOT NULL UNIQUE,  -- RFC 5322 Message-ID header
  sender        text        NOT NULL,
  subject       text,
  received_at   timestamptz NOT NULL,

  -- S3 pointer to the raw .eml / MIME blob; content is never stored in the DB
  s3_bucket     text        NOT NULL,
  s3_key        text        NOT NULL,

  -- SHA-256 of raw email bytes; unique constraint is the idempotency guard
  content_hash  text        NOT NULL UNIQUE,

  status        text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                              'pending',    -- queued for extraction
                              'processing', -- extraction in flight
                              'extracted',  -- at least one successful run
                              'failed',     -- all extraction attempts errored
                              'skipped'     -- intentionally excluded (e.g. OOO)
                            )),

  error_message text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- content_hash: covered by the UNIQUE constraint index above.
-- status: used to drain the processing queue.
CREATE INDEX emails_status_idx ON emails(status);

COMMIT;
