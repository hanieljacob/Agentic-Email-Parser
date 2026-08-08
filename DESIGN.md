# Design notes

Implementation detail behind the [README](README.md). The README covers what the system does and why the pipeline is shaped the way it is; this covers how it is put together, and the things that would otherwise cost an hour to rediscover.

---

## Stack

| Concern     | Choice                                                                                                  |
| ----------- | ------------------------------------------------------------------------------------------------------- |
| Backend     | FastAPI — one app, one uvicorn process                                                                  |
| Database    | PostgreSQL via psycopg 3 (async pool). Raw SQL, no ORM                                                  |
| Validation  | Pydantic v2 — the same models validate LLM output and shape API responses                               |
| Settings    | pydantic-settings, one `Settings` object (`backend/config.py`)                                          |
| LLM         | OpenRouter through the OpenAI-compatible Python client, behind a provider protocol with an offline stub |
| Attachments | pypdf · python-docx · openpyxl                                                                          |
| Frontend    | **React 19**                                                                                            |
| Framework   | TanStack Start — SSR, build, and file-based routing via TanStack Router                                 |
| UI          | shadcn/ui (radix base) on Tailwind CSS v4, lucide-react icons                                           |
| Build       | Vite 8                                                                                                  |
| Tests       | pytest against a real PostgreSQL database                                                               |

**React vs TanStack.** Not alternatives. React is the UI library; TanStack Start is the framework around it, filling the role Next.js would. Every component in `src/` is an ordinary React 19 component.

---

## Layout

```
backend/
  main.py          FastAPI app — lifespan opens the pool and starts the worker
  config.py        every environment knob, typed, in one place
  db.py            psycopg 3 async pool
  schemas.py       Pydantic models for LLM output + the JSON schema sent to the model
  prompt.py        SYSTEM_PROMPT and the supplier-context formatter
  llm.py           provider protocol: OpenRouterProvider | StubProvider
  ingest.py        RFC 822 → emails row (+ attachments)
  extract.py       email → context → LLM → extraction_runs row
  match.py         extraction_run → proposed_changes, with confidence scoring
  writeback.py     the only writer to canonical data
  learning.py      assign_supplier / correct_sku
  worker.py        retry loop, runs inside the API process
  pipeline.py      extract → match, the one definition of "process this email"
  suppliers.py     sender address → supplier
  routers/         emails · proposed_changes · monitoring
  scripts/         seed.py · load_fixtures.py
  fixtures/        committed demo emails + their canned extractions
  tests/
src/
  lib/api.ts       typed client — the frontend's only route to data
  routes/          index (compose) · review · monitoring
  components/ui/   shadcn components
```

The frontend holds no database connection and writes no SQL. Everything goes through `src/lib/api.ts` to FastAPI.

---

## Decisions worth knowing

**One process, not four.** Ingest, extraction, the REST API and the retry worker used to be four services on three ports in two languages. They are now one FastAPI app; the worker is an `asyncio` task started in the lifespan. Fewer moving parts to explain, and `pnpm api` is the whole backend.

**Raw SQL, no ORM.** The schema is migration-first and the queries are the interesting part — the confidence join, the `FOR UPDATE` version check, the monitoring views. An ORM would hide exactly what is worth showing. `psycopg.sql.Identifier` covers the one place a column name is dynamic.

**The Pydantic models do double duty.** `ExtractionOutput` is converted to a JSON schema and sent as `response_format`, constraining the model at generation time, and the same model validates the response before anything reaches the database. Belt and braces, because structured-output support varies by model on OpenRouter.

**Provider behind a protocol.** `ExtractionProvider` has one method. That is what makes the demo runnable offline, the tests deterministic and free, and a provider swap a one-file change. OpenRouter and Ollama are the same class with different hosts, since both speak the OpenAI API.

**`model_version` travels with the response, not the provider.** `complete()` returns a `Completion(text, model_version)`. With a fallback chain the provider that answered is not known until it has, and reading it off shared provider state afterwards would race between concurrent extractions. Returning it means every `extraction_runs` row names the model that actually produced its output.

**Fallback falls through on availability, not on quality.** `FallbackProvider` tries each provider in order and returns the first that answers. A raised error — dead key, rate limit, unreachable host — moves to the next. A response that comes back but fails validation does not: that is a modelling problem, and retrying it against a different model would hide a bad prompt behind a second opinion. Validation happens in `extract()`, after the chain has returned.

**Async throughout.** psycopg's async pool, `AsyncOpenAI`, async route handlers. The work is almost entirely I/O — Postgres and one slow HTTP call — so this is the shape that lets one process handle concurrent extractions.

**Tests hit a real database.** The properties worth testing are transactional: optimistic locking, audit rows, status transitions, two proposals racing on one row. A mocked connection would only assert that the mock was called. `backend/tests/conftest.py` drops, creates and migrates a test database per run, and pins `LLM_PROVIDER=stub` before any settings are read, so a test run can never make a billable call.

---

## Gotchas

1. **`AUTO_APPLY_THRESHOLD=0` does not disable auto-apply.** The comparison is `>=`, so `0` matches everything and empties the review queue. Use a value above `1.0` to route everything to a human. Pinned by a test.

2. **Model output that is not JSON is recorded as a _successful_, empty extraction.** `{}` validates because every top-level field has a default, so a refusal reads as "no PO updates found" rather than as an error, and is never retried. Schema _violations_ are handled properly. Carried over from the TypeScript implementation deliberately, and pinned by a test so it stays visible rather than accidental.

3. **`normalize_ref` collapses zero runs anywhere, not just leading ones.** `PO-10`, `PO-010`, `PO-100` and `PO-1000` all normalise to `10`; `PO-12` collides with `PO-102`. Ported verbatim. Contained by the confidence model: a fuzzy PO hit caps combined confidence at 0.9, below the 0.95 threshold, so a mis-resolved reference always lands in review.

4. **`LLM_PROVIDER=auto` uses OpenRouter the moment a key exists in the environment.** `.env.example` sets `stub` explicitly for that reason, and `load_fixtures.py` constructs the stub directly rather than reading the setting — otherwise the demo would depend on the machine it runs on.

5. **Plugin order in `vite.config.ts`:** `tanstackStart()` must come before `viteReact()`, or route generation breaks.

6. **`src/routeTree.gen.ts` is generated.** Never edit it; `pnpm dev` regenerates it.

7. **shadcn's generated `sonner.tsx` imports `next-themes`.** This is not a Next app and already has a theme mechanism, so it was rewritten to read the `light`/`dark` class the inline script in `__root.tsx` stamps on `<html>` before hydration. Re-running the shadcn generator will overwrite that file.

8. **shadcn's `DialogContent` defaults to `sm:max-w-sm`.** Overriding it needs a `sm:` variant of its own — a bare `max-w-3xl` loses to it.

9. **pnpm needs `@rsbuild/core` explicitly.** `@tanstack/start-plugin-core` eagerly imports the rsbuild adapter while Vite loads its config. npm's hoisting hides this; pnpm's strict isolation surfaces it as a build error, so it is a devDependency despite being otherwise unused.

10. **`pnpm seed` is destructive.** It truncates the pipeline tables as well as the canonical ones, so re-running produces an identical demo state rather than stacking a second queue.

---

## What TanStack Start is still doing

Worth being able to answer, since the port removed most of what it was originally there for. All the `createServerFn` handlers are gone — the frontend talks to FastAPI over HTTP — so Start now provides SSR, the file-based router and the build. Loaders run on the server for the first paint and on the client for later navigations, which is why `src/lib/api.ts` uses absolute URLs.

A plain Vite + React SPA would also work here, with a smaller dependency footprint. Start earns its place through the router's typed loaders, `pendingComponent` and `errorComponent`: the review queue's loading and error states come from the router rather than hand-rolled state.

---

## Colour

The review queue's palette is semantic, not decorative. Confidence bands are a status encoding — green `≥ 0.9`, amber `0.75–0.9`, red below — validated for colour-vision deficiency (worst adjacent pair ΔE 11.3 simulated, 27.6 normal vision). Colour is never the only channel: every band also shows the percentage and a meter whose length encodes the same number.

Field types take validated _categorical_ slots instead, so a field can never read as a severity. Tokens live at the top of `src/styles.css`; the `tint` value is the pure hue used for fills and borders, and `ink` is a darkened step for text and meter bars, so both clear 4.5:1 against the card in either theme.
