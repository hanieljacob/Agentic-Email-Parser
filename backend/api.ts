import express from 'express'
import cors from 'cors'
import proposedChangesRouter from '../src/routes/proposedChanges.js'
import emailsRouter from '../src/routes/emails.js'

const app = express()
const PORT = Number(process.env.API_PORT ?? 8002)

app.use(cors())
app.use(express.json())

app.use('/proposed-changes', proposedChangesRouter)
app.use('/emails', emailsRouter)

app.get('/health', (_req, res) => res.json({ ok: true }))

app.listen(PORT, () => {
  console.log(`api listening on http://localhost:${PORT}`)
})
