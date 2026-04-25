-- =============================================================================
-- 0009_schema_restructure.sql
-- Replace plural canonical tables with singular ones shaped to match db.xlsx.
-- Kept intact: emails, extraction_runs, proposed_changes, audit_log,
--              supplier_email_aliases.
-- =============================================================================

BEGIN;

-- ─── New canonical tables ────────────────────────────────────────────────────
-- increment_version() and set_updated_at() already exist from earlier migrations.

CREATE TABLE product (
  id         uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_id  integer,                        -- retained for seed traceability
  sku        text          NOT NULL UNIQUE,
  title      text,
  version    integer       NOT NULL DEFAULT 1,
  created_at timestamptz   NOT NULL DEFAULT now(),
  updated_at timestamptz   NOT NULL DEFAULT now()
);
CREATE TRIGGER product_version
  BEFORE UPDATE ON product
  FOR EACH ROW EXECUTE FUNCTION increment_version();

CREATE TABLE supplier (
  id         uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_id  integer,
  name       text          NOT NULL,
  email      text          NOT NULL UNIQUE,  -- primary resolution; aliases are secondary
  version    integer       NOT NULL DEFAULT 1,
  created_at timestamptz   NOT NULL DEFAULT now(),
  updated_at timestamptz   NOT NULL DEFAULT now()
);
CREATE TRIGGER supplier_version
  BEFORE UPDATE ON supplier
  FOR EACH ROW EXECUTE FUNCTION increment_version();

CREATE TABLE purchase_order (
  id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_id     integer,
  reference_num text        NOT NULL UNIQUE,
  supplier_id   uuid        NOT NULL REFERENCES supplier(id) ON DELETE RESTRICT,
  delivery_date date,
  version       integer     NOT NULL DEFAULT 1,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX purchase_order_supplier_id_idx ON purchase_order(supplier_id);
CREATE TRIGGER purchase_order_version
  BEFORE UPDATE ON purchase_order
  FOR EACH ROW EXECUTE FUNCTION increment_version();

CREATE TABLE purchase_order_line (
  id                uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  legacy_id         integer,
  purchase_order_id uuid          NOT NULL REFERENCES purchase_order(id) ON DELETE RESTRICT,
  product_id        uuid          NOT NULL REFERENCES product(id)         ON DELETE RESTRICT,
  quantity          numeric(18,4) NOT NULL,
  delivery_date     date,
  version           integer       NOT NULL DEFAULT 1,
  created_at        timestamptz   NOT NULL DEFAULT now(),
  updated_at        timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX purchase_order_line_po_idx      ON purchase_order_line(purchase_order_id);
CREATE INDEX purchase_order_line_product_idx ON purchase_order_line(product_id);
CREATE TRIGGER purchase_order_line_version
  BEFORE UPDATE ON purchase_order_line
  FOR EACH ROW EXECUTE FUNCTION increment_version();

CREATE TABLE supplier_product (
  supplier_id    uuid          NOT NULL REFERENCES supplier(id) ON DELETE RESTRICT,
  product_id     uuid          NOT NULL REFERENCES product(id)  ON DELETE RESTRICT,
  supplier_sku   text,
  price_per_unit numeric(18,4),
  created_at     timestamptz   NOT NULL DEFAULT now(),
  updated_at     timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (supplier_id, product_id)
);
-- Efficient lookup by supplier's own product code in the matcher
CREATE INDEX supplier_product_sku_idx ON supplier_product(supplier_id, supplier_sku);
CREATE TRIGGER supplier_product_updated_at
  BEFORE UPDATE ON supplier_product
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── Rewire supplier_email_aliases to new supplier table ─────────────────────

-- Stale rows reference old supplier UUIDs; seed will repopulate.
DELETE FROM supplier_email_aliases;
ALTER TABLE supplier_email_aliases
  DROP CONSTRAINT supplier_email_aliases_supplier_id_fkey;
ALTER TABLE supplier_email_aliases
  ADD CONSTRAINT supplier_email_aliases_supplier_id_fkey
    FOREIGN KEY (supplier_id) REFERENCES supplier(id) ON DELETE RESTRICT;

-- ─── Update proposed_changes to use singular table names ─────────────────────

-- Remove test-only rows before changing the constraint.
DELETE FROM audit_log;
DELETE FROM proposed_changes;

ALTER TABLE proposed_changes DROP CONSTRAINT proposed_changes_target_table_check;
ALTER TABLE proposed_changes ADD CONSTRAINT proposed_changes_target_table_check
  CHECK (target_table IN ('purchase_order', 'purchase_order_line'));

-- ─── Drop old plural tables ───────────────────────────────────────────────────

DROP TABLE IF EXISTS product_aliases;
DROP TABLE IF EXISTS purchase_order_lines;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS products;
-- supplier_email_aliases FK was already swapped above
DROP TABLE IF EXISTS suppliers;

COMMIT;
