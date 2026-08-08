# Demo fixtures

Each `.eml` is a supplier email; the `.json` beside it is the extraction the
offline stub provider returns for that email, keyed by its `Subject` header.

They exist so the demo is reproducible. `pnpm seed` ingests all three through
the real pipeline — the same `extract` → `match` → writeback path a live email
takes — with the stub standing in for the model. No API key, no network call,
and the same review queue on every machine.

| Fixture                    | Supplier       | What it exercises                                                         | Outcome                 |
| -------------------------- | -------------- | ------------------------------------------------------------------------- | ----------------------- |
| `01-delivery-confirmation` | Big Supplier   | Unambiguous statement, exact PO reference, exact SKU                      | 1.00 → **auto-applied** |
| `02-partial-shipment`      | Big Supplier   | Supplier's own product code `SKU13` resolved through `supplier_product`   | 0.90 → **review**       |
| `03-provisional-schedule`  | Small Supplier | Hedged wording ("sometime in early March") the model is told to score 0.8 | 0.80 → **review**       |

The scores are not hardcoded. Each fixture's `.json` supplies only the
_extraction_ confidence; the matcher computes the rest from how cleanly the PO
reference and SKU resolve, and the routing decision falls out of the
`AUTO_APPLY_THRESHOLD` comparison. Changing the threshold in `.env` changes
which of these land in the queue.

## Adding one

Drop in a `<name>.eml` and a `<name>.json` with the same stem. The subject line
is the lookup key, so it has to be unique across fixtures. An email with no
matching fixture returns an empty extraction rather than failing, which is the
same thing the real pipeline does for a message containing no PO updates.

## The stub is not the live model

Fixture `02-partial-shipment` scores 0.90 under the stub because the stub
returns the supplier's own code, `SKU13`, which the matcher then resolves
through `supplier_product` — an alias hit, worth 0.9.

A capable live model usually does not do that. The prompt context already
lists the supplier's product codes, so it resolves `SKU13` to `SKU-1-3`
itself and returns the canonical SKU. That is an exact match, worth 1.0, and
the same email auto-applies instead of landing in the queue. Verified against
both `nvidia/nemotron-3-super-120b-a12b:free` and
`nvidia/nemotron-3-ultra-550b-a55b:free`, so it is a property of the design
rather than a quirk of one model:

```
SKU-1-3  quantity -> 12000  applied  extraction=1.0 match=1.0 combined=1.0
```

Neither result is wrong — the alias table is a fallback for when the model
does not resolve the code, and a better model needs it less often. It does
mean the demo queue is deliberately more conservative than the live pipeline,
so the fixtures show the review path clearly rather than optimistically.
