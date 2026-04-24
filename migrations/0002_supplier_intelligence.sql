-- =============================================================================
-- 0002_supplier_intelligence.sql
-- Learned mappings that improve extraction matching over time.
--
-- supplier_email_aliases  — resolves an inbound sender address to a supplier.
-- product_aliases         — maps a supplier's own part code to a canonical SKU.
--
-- Both tables grow as new senders / codes are encountered and confirmed.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- supplier_email_aliases
-- When an email arrives, the ingestion layer looks up sender here to
-- identify the supplier without parsing the email body.
-- ---------------------------------------------------------------------------
CREATE TABLE supplier_email_aliases (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_id   uuid        NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
  email_address text        NOT NULL UNIQUE,  -- exact match; case folding in application
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX supplier_email_aliases_supplier_id_idx ON supplier_email_aliases(supplier_id);

-- ---------------------------------------------------------------------------
-- product_aliases
-- Suppliers use their own part numbers; this table maps them to our SKUs.
-- A supplier can have multiple aliases for the same product (e.g. regional codes).
-- ---------------------------------------------------------------------------
CREATE TABLE product_aliases (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  supplier_id uuid        NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
  product_id  uuid        NOT NULL REFERENCES products(id)  ON DELETE RESTRICT,
  alias       text        NOT NULL,  -- supplier's own part number / code
  created_at  timestamptz NOT NULL DEFAULT now(),

  UNIQUE (supplier_id, alias)   -- alias is unique per supplier, not globally
);

CREATE INDEX product_aliases_supplier_id_idx ON product_aliases(supplier_id);
CREATE INDEX product_aliases_product_id_idx  ON product_aliases(product_id);

COMMIT;
