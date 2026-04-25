-- =============================================================================
-- 0010_superseded_status.sql
-- Add 'superseded' to proposed_changes status domain.
-- A proposed_change is superseded when the target record's version has already
-- been bumped by another applied change before this one could be written back.
-- =============================================================================

BEGIN;

ALTER TABLE proposed_changes DROP CONSTRAINT proposed_changes_status_check;
ALTER TABLE proposed_changes ADD CONSTRAINT proposed_changes_status_check
  CHECK (status IN (
    'pending',     -- awaiting review
    'approved',    -- reviewer approved; queued for writeback
    'applied',     -- written to canonical table
    'rejected',    -- reviewer rejected; no write
    'superseded'   -- target version changed before this was applied
  ));

COMMIT;
