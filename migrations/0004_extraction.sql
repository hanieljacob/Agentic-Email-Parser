-- =============================================================================
-- 0004_extraction.sql
-- One row per LLM extraction attempt on an email. An email can have many runs
-- (reprocess after a model upgrade, manual retry, prompt change, etc.).
-- The raw LLM output is preserved verbatim for debugging and eval.
-- =============================================================================

BEGIN;

CREATE TABLE extraction_runs (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  email_id      uuid        NOT NULL REFERENCES emails(id) ON DELETE RESTRICT,
  model_version text        NOT NULL,   -- e.g. "claude-3-5-sonnet-20241022"

  -- Full raw response from the LLM; never truncated
  llm_output    jsonb,

  status        text        NOT NULL
                            CHECK (status IN ('success', 'error', 'timeout')),
  error_message text,

  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX extraction_runs_email_id_idx ON extraction_runs(email_id);

COMMIT;
