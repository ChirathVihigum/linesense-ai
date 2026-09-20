import { z } from 'zod'

const EXTERNAL_REF_MESSAGE =
  'Use 3 to 40 characters: capital letters, digits and hyphens, starting with a letter or digit.'
const QUANTITY_MESSAGE = 'Enter a whole number from 1 to 1,000,000.'
const PRIORITY_MESSAGE = 'Priority must be 1 (highest) to 5.'

/** Mirrors `OrderCreate` in services/backend/app/api/schemas/orders.py. */
export const orderCreateSchema = z.object({
  external_ref: z
    .string()
    .min(3, EXTERNAL_REF_MESSAGE)
    .max(40, EXTERNAL_REF_MESSAGE)
    .regex(/^[A-Z0-9][A-Z0-9-]*$/, EXTERNAL_REF_MESSAGE),
  customer_id: z.string().min(1, 'Select a customer.'),
  style_id: z.string().min(1, 'Select a style.'),
  quantity: z
    .number({ error: QUANTITY_MESSAGE })
    .int(QUANTITY_MESSAGE)
    .min(1, QUANTITY_MESSAGE)
    .max(1_000_000, QUANTITY_MESSAGE),
  due_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'Enter a due date.'),
  priority: z
    .number({ error: PRIORITY_MESSAGE })
    .int(PRIORITY_MESSAGE)
    .min(1, PRIORITY_MESSAGE)
    .max(5, PRIORITY_MESSAGE),
})

export type OrderCreateValues = z.infer<typeof orderCreateSchema>
export const ORDER_FORM_FIELDS = new Set<string>(Object.keys(orderCreateSchema.shape))

/** Where to go after creating an order: its detail page. */
export function orderCreatedPath(factoryCode: string, orderId: string): string {
  return `/f/${encodeURIComponent(factoryCode)}/orders/${orderId}`
}
