#!/usr/bin/env tsx
/**
 * Retry worker: periodically retries emails that are stuck in the pipeline.
 *
 * Emails end up stuck when the extract server was down at ingestion time
 * (status='ingested') or when a previous extraction attempt failed (status='failed').
 *
 * Entry point:
 *   tsx backend/worker.ts
 *
 * Env vars (all optional — defaults work for local dev):
 *   EXTRACT_SERVER_URL      default http://localhost:8001
 *   WORKER_INTERVAL_SECONDS default 60
 *   WORKER_GRACE_SECONDS    default 120  (skip emails ingested less than N seconds ago)
 *   WORKER_MAX_RETRIES      default 3    (don't retry failed emails more than N times)
 */

import { pool } from './db.js'

const EXTRACT_SERVER_URL    = process.env.EXTRACT_SERVER_URL ?? 'http://localhost:8001'
const INTERVAL_SECONDS      = Number(process.env.WORKER_INTERVAL_SECONDS ?? 60)
const GRACE_SECONDS         = Number(process.env.WORKER_GRACE_SECONDS    ?? 120)
const MAX_RETRIES           = Number(process.env.WORKER_MAX_RETRIES      ?? 3)

// ── email discovery ───────────────────────────────────────────────────────────

interface StuckEmail {
  id: string
  status: string
  sender: string
  attempt_count: number
}

async function findStuckEmails(): Promise<StuckEmail[]> {
  const { rows } = await pool.query<StuckEmail>(
    `SELECT
       e.id,
       e.status,
       e.sender,
       count(er.id)::integer AS attempt_count
     FROM emails e
     LEFT JOIN extraction_runs er ON er.email_id = e.id
     WHERE (
       -- Ingested but pipeline never started, or extract server was down
       e.status = 'ingested'
       AND e.received_at < now() - make_interval(secs => $1)
       AND NOT EXISTS (
         SELECT 1 FROM extraction_runs er2
         WHERE er2.email_id = e.id
           AND er2.created_at > now() - interval '10 minutes'
       )
     ) OR (
       -- Previously failed but still under the retry limit
       e.status = 'failed'
       AND e.received_at < now() - interval '5 minutes'
     )
     GROUP BY e.id, e.status, e.sender
     HAVING count(er.id) < $2
     LIMIT 20`,
    [GRACE_SECONDS, MAX_RETRIES],
  )
  return rows
}

// ── pipeline trigger ──────────────────────────────────────────────────────────

async function triggerPipeline(emailId: string): Promise<void> {
  const res = await fetch(`${EXTRACT_SERVER_URL}/pipeline`, {
    method: 'POST',
    body: emailId,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`pipeline ${res.status}: ${body}`)
  }
}

// ── worker loop ───────────────────────────────────────────────────────────────

async function tick(): Promise<void> {
  const stuck = await findStuckEmails()
  if (stuck.length === 0) return

  console.log(`[worker] ${new Date().toISOString()} — retrying ${stuck.length} stuck email(s)`)

  for (const email of stuck) {
    try {
      await triggerPipeline(email.id)
      console.log(
        `[worker]   ✓ retriggered ${email.id}` +
        `  status=${email.status}  attempts=${email.attempt_count}` +
        `  from=${email.sender}`,
      )
    } catch (err) {
      console.error(
        `[worker]   ✗ failed to retrigger ${email.id}:`,
        err instanceof Error ? err.message : err,
      )
    }
  }
}

async function run(): Promise<void> {
  console.log(
    `[worker] starting` +
    `  interval=${INTERVAL_SECONDS}s` +
    `  grace=${GRACE_SECONDS}s` +
    `  max_retries=${MAX_RETRIES}` +
    `  extract=${EXTRACT_SERVER_URL}`,
  )

  // Run once immediately on startup to catch anything stuck before the worker launched.
  await tick().catch((err) => console.error('[worker] initial tick error:', err))

  setInterval(() => {
    tick().catch((err) => console.error('[worker] tick error:', err))
  }, INTERVAL_SECONDS * 1000)
}

run().catch((err) => {
  console.error('[worker] fatal:', err)
  process.exit(1)
})
