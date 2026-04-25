-- =============================================================================
-- 0011_email_attachments.sql
-- Track per-email attachments saved to the local ./attachments/ directory.
-- =============================================================================

BEGIN;

CREATE TABLE email_attachments (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  email_id      uuid        NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
  stored_name   text        NOT NULL,  -- filename on disk: <sha256><ext>
  original_name text,                  -- filename from Content-Disposition header
  mime_type     text        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX email_attachments_email_id_idx ON email_attachments(email_id);

COMMIT;
