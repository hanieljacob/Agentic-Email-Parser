-- =============================================================================
-- 0005_staging.sql
-- proposed_changes: the core staging layer.
--
-- One row per field-level proposed update to a canonical record. Nothing
-- writes to canonical tables from the extractor directly. Every write must
-- pass through here and be either auto-applied (high confidence) or approved
-- by a reviewer.
--
-- Concurrency: target_record_version captures the canonical record's version
-- at extraction time. The apply path checks this value — see README.
-- =============================================================================

BEGIN;

CREATE TABLE proposed_changes (
  id                    uuid         PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Source traceability
  email_id              uuid         NOT NULL REFERENCES emails(id)          ON DELETE RESTRICT,
  extraction_run_id     uuid         NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,

  -- Target: which table and row this change applies to.
  -- target_record_id is a logical FK (purchase_orders.id or
  -- purchase_order_lines.id); Postgres cannot express polymorphic FKs so
  -- referential integrity is enforced by the application before writeback.
  target_table          text         NOT NULL
                                     CHECK (target_table IN (
                                       'purchase_orders',
                                       'purchase_order_lines'
                                     )),
  target_record_id      uuid         NOT NULL,
  -- Version of the canonical record when this proposal was created.
  -- Checked at writeback; mismatch means another change was applied first.
  target_record_version integer      NOT NULL,
  field_name            text         NOT NULL,

  -- Values (stored as text; cast to domain type at writeback)
  old_value             text,        -- snapshot at extraction time; shown to reviewer
  new_value             text         NOT NULL,

  -- Evidence shown to reviewer
  evidence_text         text,        -- verbatim span from email or attachment
  evidence_metadata     jsonb,       -- page, char offsets, bounding box, etc.

  -- Confidence scores (0–1)
  extraction_confidence numeric(4,3) NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
  match_confidence      numeric(4,3) NOT NULL CHECK (match_confidence      BETWEEN 0 AND 1),
  combined_confidence   numeric(4,3) NOT NULL CHECK (combined_confidence   BETWEEN 0 AND 1),

  -- Review
  status                text         NOT NULL DEFAULT 'pending'
                                     CHECK (status IN (
                                       'pending',   -- awaiting routing / review
                                       'approved',  -- reviewer approved; awaiting writeback
                                       'applied',   -- written to canonical table
                                       'rejected'   -- reviewer rejected; no write
                                     )),
  reviewer_id           text,        -- opaque user identifier; no users table yet
  review_notes          text,
  reviewed_at           timestamptz,

  created_at            timestamptz  NOT NULL DEFAULT now(),
  updated_at            timestamptz  NOT NULL DEFAULT now()
);

-- Queue drain: all pending proposals
CREATE INDEX proposed_changes_status_idx          ON proposed_changes(status);
-- Trace all proposals for an email or a specific extraction run
CREATE INDEX proposed_changes_email_id_idx        ON proposed_changes(email_id);
CREATE INDEX proposed_changes_extraction_run_idx  ON proposed_changes(extraction_run_id);
-- Look up all proposals targeting a canonical record
CREATE INDEX proposed_changes_target_record_idx   ON proposed_changes(target_table, target_record_id);

-- Keep updated_at current
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER proposed_changes_set_updated_at
  BEFORE UPDATE ON proposed_changes
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
