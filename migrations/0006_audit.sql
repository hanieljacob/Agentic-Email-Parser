-- =============================================================================
-- 0006_audit.sql
-- Immutable record of every change applied to canonical data.
-- Rows are never updated or deleted; triggers enforce this.
--
-- To reverse an applied change: create a new proposed_changes row with the
-- prior_value as new_value and run it through the normal apply path. The
-- reversal itself will produce an audit_log row. Never revert by direct UPDATE.
-- =============================================================================

BEGIN;

CREATE TABLE audit_log (
  id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),

  -- What changed
  target_table       text        NOT NULL,
  target_record_id   uuid        NOT NULL,
  field_name         text        NOT NULL,
  prior_value        text,        -- null if field was previously unset
  new_value          text        NOT NULL,

  -- Who/what applied it
  -- 'auto' for confidence-threshold auto-apply; otherwise an opaque user id
  applied_by         text        NOT NULL,

  -- Link back to the proposal that authorised this write
  proposed_change_id uuid        NOT NULL REFERENCES proposed_changes(id) ON DELETE RESTRICT,

  applied_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Immutability: raise on any UPDATE or DELETE attempt
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit_log_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log rows are immutable (id=%)', OLD.id;
END;
$$;

CREATE TRIGGER audit_log_no_update
  BEFORE UPDATE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

CREATE TRIGGER audit_log_no_delete
  BEFORE DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Full history for a canonical record
CREATE INDEX audit_log_target_record_idx   ON audit_log(target_table, target_record_id);
-- Trace from audit entry back to the proposal that caused it
CREATE INDEX audit_log_proposed_change_idx ON audit_log(proposed_change_id);

COMMIT;
