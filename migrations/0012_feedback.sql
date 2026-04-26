-- =============================================================================
-- 0012_feedback.sql
-- Structured rejection feedback loop and supplier-specific LLM learning.
-- =============================================================================

BEGIN;

-- 1. Capture why a proposed change was rejected so patterns can be aggregated.
ALTER TABLE proposed_changes
  ADD COLUMN rejection_reason text
  CHECK (rejection_reason IN (
    'wrong_date_format',
    'wrong_sku',
    'not_a_po_update',
    'quantity_is_delta',
    'wrong_po_reference',
    'llm_hallucination',
    'other'
  ));

-- 2. Free-text notes injected verbatim into the LLM prompt for this supplier.
--    A developer sets this after reviewing aggregated rejection patterns.
--    Example: "This supplier always writes dates in DD/MM/YYYY format."
ALTER TABLE supplier
  ADD COLUMN llm_notes text;

-- 3. Per-supplier few-shot correction examples included in the extraction prompt.
--    Populated automatically by the correct-sku flow (and future correction flows).
CREATE TABLE supplier_corrections (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_id  uuid        NOT NULL REFERENCES supplier(id) ON DELETE RESTRICT,
  context      text        NOT NULL,  -- verbatim evidence phrase that was misread
  wrong        text        NOT NULL,  -- what the LLM extracted
  correct      text        NOT NULL,  -- what it should have been
  field        text        NOT NULL,  -- 'sku_or_code' | 'delivery_date' | 'quantity'
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Efficiently load the N most recent corrections for a supplier at extraction time.
CREATE INDEX supplier_corrections_supplier_idx
  ON supplier_corrections(supplier_id, created_at DESC);

COMMIT;
