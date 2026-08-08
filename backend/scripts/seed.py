"""Seed canonical tables from db.xlsx.

Reads backend/data/db.xlsx (or XLSX_PATH), truncates the canonical tables
and reloads them, mapping the workbook's integer ids onto UUIDs.

Safe to run repeatedly in development.

    python -m backend.scripts.seed
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.config import PROJECT_ROOT
from backend.db import close_pool, connection, open_pool


def _rows(worksheet: Any) -> list[dict[str, Any]]:
    """Read a sheet as dicts keyed by its header row, skipping blank rows."""
    it = worksheet.iter_rows(values_only=True)
    try:
        header = [str(h) if h is not None else "" for h in next(it)]
    except StopIteration:
        return []

    out: list[dict[str, Any]] = []
    for row in it:
        if all(cell is None for cell in row):
            continue
        out.append({key: value for key, value in zip(header, row) if key})
    return out


def _int(value: Any) -> int | None:
    """Workbook integers arrive as floats (1.0); normalise them."""
    return None if value is None else int(value)


def _date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def _nullify(value: Any) -> Any:
    """The workbook uses '' and '-' to mean "no value"."""
    if value is None or value == "" or value == "-":
        return None
    return value


async def seed() -> None:
    xlsx_path = Path(
        os.environ.get("XLSX_PATH", PROJECT_ROOT / "backend" / "data" / "db.xlsx")
    )
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)

    products = _rows(workbook["product"])
    suppliers = _rows(workbook["supplier"])
    purchase_orders = _rows(workbook["purchase_order"])
    lines = _rows(workbook["purchase_order_line"])
    supplier_products = _rows(workbook["supplier_product"])
    workbook.close()

    print(
        f"xlsx loaded: {len(products)} products, {len(suppliers)} suppliers, "
        f"{len(purchase_orders)} POs, {len(lines)} lines, "
        f"{len(supplier_products)} supplier_products"
    )

    async with connection() as conn:
        async with conn.transaction():
            # Truncate in reverse FK dependency order; CASCADE handles children.
            await conn.execute(
                """
                TRUNCATE supplier_email_aliases, supplier_product,
                         purchase_order_line, purchase_order,
                         product, supplier
                RESTART IDENTITY CASCADE
                """
            )

            product_ids: dict[int, str] = {}
            supplier_ids: dict[int, str] = {}
            po_ids: dict[int, str] = {}

            for row in products:
                cur = await conn.execute(
                    "INSERT INTO product (legacy_id, sku, title) VALUES (%s,%s,%s) RETURNING id",
                    (_int(row["id"]), row["sku"], _nullify(row["title"])),
                )
                product_ids[_int(row["id"])] = (await cur.fetchone())["id"]
            print(f"  ✓ {len(products)} products")

            for row in suppliers:
                cur = await conn.execute(
                    "INSERT INTO supplier (legacy_id, name, email) VALUES (%s,%s,%s) RETURNING id",
                    (_int(row["id"]), row["name"], row["email"]),
                )
                supplier_id = (await cur.fetchone())["id"]
                supplier_ids[_int(row["id"])] = supplier_id

                # Seed the primary email as an alias so alias resolution works too.
                await conn.execute(
                    "INSERT INTO supplier_email_aliases (supplier_id, email_address) VALUES (%s,%s)",
                    (supplier_id, row["email"]),
                )
            print(f"  ✓ {len(suppliers)} suppliers (+ {len(suppliers)} email aliases)")

            for row in purchase_orders:
                supplier_id = supplier_ids.get(_int(row["supplier_id"]))
                if supplier_id is None:
                    raise ValueError(f"Unknown supplier legacy_id: {row['supplier_id']}")
                cur = await conn.execute(
                    """
                    INSERT INTO purchase_order
                      (legacy_id, reference_num, supplier_id, delivery_date)
                    VALUES (%s,%s,%s,%s) RETURNING id
                    """,
                    (
                        _int(row["id"]),
                        row["reference_num"],
                        supplier_id,
                        _date(row["delivery_date"]),
                    ),
                )
                po_ids[_int(row["id"])] = (await cur.fetchone())["id"]
            print(f"  ✓ {len(purchase_orders)} purchase_orders")

            for row in lines:
                po_id = po_ids.get(_int(row["purchase_order_id"]))
                product_id = product_ids.get(_int(row["product_id"]))
                if po_id is None:
                    raise ValueError(f"Unknown PO legacy_id: {row['purchase_order_id']}")
                if product_id is None:
                    raise ValueError(f"Unknown product legacy_id: {row['product_id']}")
                await conn.execute(
                    """
                    INSERT INTO purchase_order_line
                      (legacy_id, purchase_order_id, product_id, quantity, delivery_date)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (
                        _int(row["id"]),
                        po_id,
                        product_id,
                        row["quantity"],
                        _date(row["delivery_date"]),
                    ),
                )
            print(f"  ✓ {len(lines)} purchase_order_lines")

            for row in supplier_products:
                supplier_id = supplier_ids.get(_int(row["supplier_id"]))
                product_id = product_ids.get(_int(row["product_id"]))
                if supplier_id is None or product_id is None:
                    raise ValueError(f"Unknown ids in supplier_product row: {row}")
                await conn.execute(
                    """
                    INSERT INTO supplier_product
                      (supplier_id, product_id, supplier_sku, price_per_unit)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (
                        supplier_id,
                        product_id,
                        _nullify(row["supplier_sku"]),
                        _nullify(row["price_per_unit"]),
                    ),
                )
            print(f"  ✓ {len(supplier_products)} supplier_products")

    print("Seed complete.")


async def main() -> None:
    await open_pool()
    try:
        await seed()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
