# Agentic Email Parser

Automatically ingests supplier emails, extracts purchase order (PO) updates using an LLM, matches them to canonical DB records, and either auto-applies high-confidence changes or routes them to a human review queue.

---

## How it works

```
Supplier email
      │
      ▼
┌─────────────┐     RFC 822      ┌──────────────────┐
│  Ingest     │ ───────────────► │  Extract (LLM)   │
│  (Python /  │                  │  OpenRouter API  │
│  FastAPI)   │                  └────────┬─────────┘
└─────────────┘                           │ structured JSON
                                          ▼
                                 ┌──────────────────┐
                                 │  Match           │
                                 │  PO ref + SKU →  │
                                 │  DB records      │
                                 └────────┬─────────┘
                              confidence ≥ 0.95?
                                 ┌────────┴─────────┐
                                Yes                  No
                                 ▼                   ▼
                          Auto-apply           Review queue
                          (writeback)          /review UI
```

### Pipeline stages

| Stage | Entry point | What it does |
|---|---|---|
| **Ingest** | `backend/ingest.py` (FastAPI, port 8000) | Parses RFC 822 email, saves to DB, extracts attachments, fires pipeline trigger |
| **Extract** | `backend/extract.ts` (HTTP server, port 8001) | Builds LLM context from supplier history + corrections, calls OpenRouter, validates JSON output |
| **Match** | `backend/match.ts` | Resolves PO ref and SKU to canonical DB rows, scores confidence, inserts `proposed_changes` |
| **Review** | `/review` (TanStack Start UI) | Human approves or rejects pending changes with a reason |
| **Writeback** | `src/writeback/apply.ts` | Applies approved change to `purchase_order_line` with optimistic locking and audit log |

---

## Setup

### Prerequisites

- Node.js 20+, pnpm
- Python 3.12+
- PostgreSQL (local or remote)
- An [OpenRouter](https://openrouter.ai) API key

### First-time setup

```bash
# 1. Install JS dependencies
pnpm install

# 2. Install Python dependencies
pip install -r backend/requirements.txt

# 3. Copy and fill in environment variables
cp .env.example .env   # then edit .env

# 4. Create the database, run all migrations, install Python deps
pnpm setup

# 5. Seed canonical tables from backend/data/db.xlsx
pnpm seed
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL connection string |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `MODEL_NAME` | `anthropic/claude-sonnet-4` | LLM model used for extraction |
| `EXTRACT_SERVER_URL` | `http://localhost:8001` | URL of the extract server (used by ingest + worker) |
| `AUTO_APPLY_THRESHOLD` | `0.95` | Combined confidence threshold for auto-apply; set to `0` to disable |
| `ATTACHMENTS_DIR` | `./attachments` | Directory where email attachments are stored |
| `API_PORT` | `8002` | Port for the REST writeback API |
| `WORKER_INTERVAL_SECONDS` | `60` | How often the retry worker polls for stuck emails |
| `WORKER_GRACE_SECONDS` | `120` | Minimum age before a stuck email is retried |
| `WORKER_MAX_RETRIES` | `3` | Maximum pipeline retry attempts per email |

---

## Running

Start each service in a separate terminal:

```bash
pnpm ingest          # Python ingest server  →  http://localhost:8000
pnpm extract-server  # LLM extraction server →  http://localhost:8001
pnpm dev             # Frontend (TanStack Start) → http://localhost:3000
```

Optional:
```bash
pnpm worker          # Retry worker — re-triggers stuck emails automatically
pnpm api             # REST writeback API → http://localhost:8002
```

---

## Submitting an email

**Via the UI** — open `http://localhost:3000`, fill in the compose form and click Send.

**Via curl** — POST a raw `.eml` file directly to the ingest server:
```bash
curl -X POST http://localhost:8000/emails --data-binary @email.eml
```

---

## Features

### Attachment support

The extraction step reads text from all common attachment types:

| Format | Extraction method |
|---|---|
| Images (`image/*`) | Passed as base64 vision inputs to the LLM |
| PDF | Text layer extracted via `pdf-parse`; scanned PDFs get a reviewer note |
| Word (`.docx`) | Raw text via `mammoth` |
| Excel (`.xlsx`, `.xls`, `.csv`) | Each sheet converted to CSV via `xlsx` |
| Plain text | Read directly |

### Confidence-based auto-apply

Each proposed change gets a `combined_confidence` score (extraction confidence × match confidence). Changes above `AUTO_APPLY_THRESHOLD` (default 0.95) are applied immediately without human review. All changes — auto-applied or manual — are recorded in the immutable `audit_log`.

### Feedback loop

Three layers of learning that improve extraction accuracy over time:

1. **Rejection reasons** — reviewers select a structured reason when rejecting a change (`wrong_sku`, `wrong_date_format`, etc.); visible per supplier on the Monitoring page.
2. **Supplier notes** — set `supplier.llm_notes` to inject free-text guidance into the extraction prompt for that supplier.
3. **Few-shot corrections** — approving a SKU correction via the API writes to `supplier_corrections`; the next extraction for that supplier includes up to 5 recent corrections as examples.

### Retry worker

`pnpm worker` polls for emails stuck in `ingested` or `failed` status and re-triggers the pipeline, up to `WORKER_MAX_RETRIES` attempts.

### Monitoring

`http://localhost:3000/monitoring` shows:
- Pipeline health (email counts by status, green/yellow/red indicator)
- Proposed changes summary (pending, auto-applied, rejected, average confidence)
- Stuck emails table
- Rejection patterns per supplier with a prompt to set `llm_notes` when patterns emerge

---

## Database schema

### Canonical tables (seeded from `backend/data/db.xlsx`)

| Table | Key columns |
|---|---|
| `product` | `sku`, `title` |
| `supplier` | `name`, `email`, `llm_notes` |
| `purchase_order` | `reference_num`, `supplier_id`, `delivery_date` |
| `purchase_order_line` | `purchase_order_id`, `product_id`, `quantity`, `delivery_date`, `version` |
| `supplier_product` | `(supplier_id, product_id)`, `supplier_sku`, `price_per_unit` |

### Pipeline tables

| Table | Purpose |
|---|---|
| `emails` | Ingested emails with status (`ingested` → `extracted` → `matched`/`failed`) |
| `email_attachments` | Attachment metadata; files stored at `ATTACHMENTS_DIR/<sha256><ext>` |
| `extraction_runs` | LLM output per email; links emails → proposed_changes |
| `proposed_changes` | One row per field change; status: `pending`, `approved`, `applied`, `rejected`, `superseded` |
| `audit_log` | Immutable write history (insert-only, enforced by trigger) |
| `supplier_email_aliases` | Maps additional sender addresses to suppliers |
| `supplier_corrections` | Few-shot SKU correction examples injected into future prompts |

### Views (migration 0013)

| View | Purpose |
|---|---|
| `pipeline_status` | Email counts by status — used by Monitoring page |
| `rejection_patterns` | Per-supplier rejection counts by reason — used by Monitoring page |

---

## REST API

Start with `pnpm api` (port 8002).

| Method | Path | Body | Action |
|---|---|---|---|
| `POST` | `/proposed-changes/:id/apply` | `{ applied_by?: string }` | Applies change to `purchase_order_line` (version-safe; marks superseded on conflict) |
| `POST` | `/proposed-changes/:id/correct-sku` | `{ correct_product_id: string }` | Records supplier SKU mapping, re-points proposed change at correct line |
| `POST` | `/emails/:id/assign-supplier` | `{ supplier_id: string, retrigger?: boolean }` | Links sender address to supplier; optionally re-runs extract + match |
| `GET` | `/health` | — | Health check |

---

## Scripts

```bash
pnpm dev             # Start frontend dev server
pnpm ingest          # Start Python ingest server (port 8000)
pnpm extract-server  # Start LLM extract server (port 8001)
pnpm worker          # Start retry worker
pnpm api             # Start REST API server (port 8002)
pnpm migrate         # Run all SQL migrations in order
pnpm seed            # Seed canonical tables from backend/data/db.xlsx
pnpm setup           # First-time: create DB + migrate + pip install
pnpm build           # Production build
pnpm test            # Run Vitest test suite
pnpm check           # Prettier + ESLint fix
```
