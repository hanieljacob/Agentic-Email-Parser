import { Router } from 'express'
import { z } from 'zod'
import { applyProposedChange } from '../writeback/apply.js'
import { correctSku } from '../learning/aliases.js'

const router = Router()

const ApplyBody = z.object({
  applied_by: z.string().optional(),
})

const CorrectSkuBody = z.object({
  correct_product_id: z.string().uuid(),
})

router.post('/:id/apply', async (req, res) => {
  const parse = ApplyBody.safeParse(req.body)
  if (!parse.success) {
    res.status(400).json({ error: parse.error.flatten() })
    return
  }
  try {
    const result = await applyProposedChange(req.params.id, parse.data.applied_by)
    res.json(result)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    res.status(422).json({ error: msg })
  }
})

router.post('/:id/correct-sku', async (req, res) => {
  const parse = CorrectSkuBody.safeParse(req.body)
  if (!parse.success) {
    res.status(400).json({ error: parse.error.flatten() })
    return
  }
  try {
    const result = await correctSku(req.params.id, parse.data.correct_product_id)
    res.json(result)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    res.status(422).json({ error: msg })
  }
})

export default router
