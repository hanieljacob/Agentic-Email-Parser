-- =============================================================================
-- 0001_canonical.sql
-- ERP-mirrored canonical data. These tables are the source of truth for
-- matching; the extraction pipeline never writes here directly.
--
-- Every table has:
--   version integer  — incremented by trigger on each UPDATE; used by the
--                      apply path to detect concurrent modifications.
--   external_id text — the ERP's own identifier for upsert / resync.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Shared trigger: bump version + stamp updated_at on every UPDATE.
-- The apply path in the application must issue:
--   UPDATE <table> SET ... WHERE id = $id AND version = $expected_version
-- and check that exactly one row was affected. Zero rows = version mismatch.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_version()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.version    = OLD.version + 1;
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- suppliers
-- ---------------------------------------------------------------------------
CREATE TABLE suppliers (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id text        NOT NULL UNIQUE,
  name        text        NOT NULL,
  version     integer     NOT NULL DEFAULT 1,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER suppliers_version
  BEFORE UPDATE ON suppliers
  FOR EACH ROW EXECUTE FUNCTION increment_version();

-- ---------------------------------------------------------------------------
-- products
-- ---------------------------------------------------------------------------
CREATE TABLE products (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id text        NOT NULL UNIQUE,
  sku         text        NOT NULL UNIQUE,  -- canonical product code used in matching
  name        text        NOT NULL,
  version     integer     NOT NULL DEFAULT 1,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER products_version
  BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION increment_version();

-- ---------------------------------------------------------------------------
-- purchase_orders
-- reference_number is what suppliers quote in emails; primary matching signal.
-- ---------------------------------------------------------------------------
CREATE TABLE purchase_orders (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id      text        NOT NULL UNIQUE,
  supplier_id      uuid        NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
  reference_number text        NOT NULL UNIQUE,
  status           text        NOT NULL
                               CHECK (status IN ('open', 'partial', 'closed', 'cancelled')),
  order_date       date,
  version          integer     NOT NULL DEFAULT 1,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX purchase_orders_supplier_id_idx ON purchase_orders(supplier_id);

CREATE TRIGGER purchase_orders_version
  BEFORE UPDATE ON purchase_orders
  FOR EACH ROW EXECUTE FUNCTION increment_version();

-- ---------------------------------------------------------------------------
-- purchase_order_lines
-- The rows most frequently updated by supplier emails:
--   expected_delivery_date, quantity_confirmed, status.
-- ---------------------------------------------------------------------------
CREATE TABLE purchase_order_lines (
  id                     uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id            text          NOT NULL UNIQUE,
  purchase_order_id      uuid          NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
  product_id             uuid          NOT NULL REFERENCES products(id)        ON DELETE RESTRICT,
  line_number            integer       NOT NULL,
  quantity_ordered       numeric(18,4) NOT NULL,
  quantity_confirmed     numeric(18,4),
  unit_price             numeric(18,4),
  currency               char(3),      -- ISO 4217
  expected_delivery_date date,
  status                 text          NOT NULL
                                       CHECK (status IN ('open', 'partial', 'received', 'cancelled')),
  version                integer       NOT NULL DEFAULT 1,
  created_at             timestamptz   NOT NULL DEFAULT now(),
  updated_at             timestamptz   NOT NULL DEFAULT now(),

  UNIQUE (purchase_order_id, line_number)
);

CREATE INDEX purchase_order_lines_po_id_idx      ON purchase_order_lines(purchase_order_id);
CREATE INDEX purchase_order_lines_product_id_idx ON purchase_order_lines(product_id);

CREATE TRIGGER purchase_order_lines_version
  BEFORE UPDATE ON purchase_order_lines
  FOR EACH ROW EXECUTE FUNCTION increment_version();

COMMIT;
