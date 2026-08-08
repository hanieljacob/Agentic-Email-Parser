# Demo fixtures

Each `.eml` is a supplier email; the `.json` beside it is the extraction the
offline stub provider returns for that email, keyed by its `Subject` header.

They exist so the demo is reproducible. `pnpm seed` ingests all three through
the real pipeline — the same `extract` → `match` → writeback path a live email
takes — with the stub standing in for the model. No API key, no network call,
and the same review queue on every machine.

| Fixture | Supplier | What it exercises | Outcome |
|---|---|---|---|
| `01-delivery-confirmation` | Big Supplier | Unambiguous statement, exact PO reference, exact SKU | 1.00 → **auto-applied** |
| `02-partial-shipment` | Big Supplier | Supplier's own product code `SKU13` resolved through `supplier_product` | 0.90 → **review** |
| `03-provisional-schedule` | Small Supplier | Hedged wording ("sometime in early March") the model is told to score 0.8 | 0.80 → **review** |

The scores are not hardcoded. Each fixture's `.json` supplies only the
*extraction* confidence; the matcher computes the rest from how cleanly the PO
reference and SKU resolve, and the routing decision falls out of the
`AUTO_APPLY_THRESHOLD` comparison. Changing the threshold in `.env` changes
which of these land in the queue.

## Adding one

Drop in a `<name>.eml` and a `<name>.json` with the same stem. The subject line
is the lookup key, so it has to be unique across fixtures. An email with no
matching fixture returns an empty extraction rather than failing, which is the
same thing the real pipeline does for a message containing no PO updates.
