import { Router } from 'express'
import { z } from 'zod'
import { assignSupplier } from '../learning/aliases.js'

const router = Router()

const AssignSupplierBody = z.object({
  supplier_id: z.string().uuid(),
  retrigger: z.boolean().optional(),
})

router.post('/:id/assign-supplier', async (req, res) => {
  const parse = AssignSupplierBody.safeParse(req.body)
  if (!parse.success) {
    res.status(400).json({ error: parse.error.flatten() })
    return
  }
  try {
    const result = await assignSupplier(
      req.params.id,
      parse.data.supplier_id,
      parse.data.retrigger,
    )
    res.json(result)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    res.status(422).json({ error: msg })
  }
})

export default router
