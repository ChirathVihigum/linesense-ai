import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Tabs } from '../../components/Tabs'
import { fieldAria } from '../../components/fieldAria'
import { api, fetchAllPages, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'

type MaterialStatus = Schemas['MaterialStatusOut']

const LOT_CODE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/
const LOT_CODE_MESSAGE =
  'Use letters, digits, dot, underscore, slash or hyphen, starting with a letter or digit (1 to 64 characters).'
const QUANTITY_MESSAGE = 'Enter a quantity greater than 0.'

const receiptSchema = z.object({
  material_id: z.string().min(1, 'Select a material.'),
  lot_code: z.string().min(1, LOT_CODE_MESSAGE).max(64, LOT_CODE_MESSAGE).regex(LOT_CODE_PATTERN, LOT_CODE_MESSAGE),
  quantity: z.number({ error: QUANTITY_MESSAGE }).positive(QUANTITY_MESSAGE),
  accept: z.boolean(),
})
type ReceiptValues = z.infer<typeof receiptSchema>

const acceptSchema = z.object({
  material_id: z.string().min(1, 'Select a material.'),
  lot_id: z.string().min(1, 'Select a lot.'),
})
type AcceptValues = z.infer<typeof acceptSchema>

const issueSchema = z.object({
  material_id: z.string().min(1, 'Select a material.'),
  lot_id: z.string().min(1, 'Select a lot.'),
  quantity: z.number({ error: QUANTITY_MESSAGE }).positive(QUANTITY_MESSAGE),
  order_id: z.string().optional(),
  reason: z.string().max(500, 'Keep the reason under 500 characters.').optional(),
})
type IssueValues = z.infer<typeof issueSchema>

const correctionSchema = z.object({
  movement_id: z.string().min(1, 'Enter the id of the movement to correct.'),
  quantity_delta: z
    .number({ error: 'Enter a non-zero adjustment.' })
    .refine((value) => value !== 0, 'The adjustment cannot be zero.'),
  reason: z.string().min(3, 'Enter a reason (at least 3 characters).').max(500, 'Keep the reason under 500 characters.'),
})
type CorrectionValues = z.infer<typeof correctionSchema>

function useMaterialLots(factoryId: string, materialId: string) {
  return useQuery({
    queryKey: ['material-lots', factoryId, materialId],
    queryFn: async () => {
      const movements = await fetchAllPages<Schemas['MovementOut']>((offset, limit) =>
        unwrap(
          api.GET('/api/v1/factories/{factory_id}/materials/{material_id}/ledger', {
            params: { path: { factory_id: factoryId, material_id: materialId }, query: { limit, offset } },
          }),
        ),
      )
      const lots = new Map<string, string>()
      for (const movement of movements) {
        if (movement.lot_id && movement.lot_code && !lots.has(movement.lot_id)) {
          lots.set(movement.lot_id, movement.lot_code)
        }
      }
      return Array.from(lots, ([id, code]) => ({ id, code }))
    },
    enabled: materialId !== '',
    staleTime: 30_000,
  })
}

function MaterialSelect({
  id,
  materials,
  value,
  onChange,
}: {
  id: string
  materials: MaterialStatus[]
  value: string
  onChange: (materialId: string) => void
}) {
  return (
    <select
      className="input"
      {...fieldAria(id, undefined, undefined, true)}
      value={value}
      onChange={(event) => {
        onChange(event.target.value)
      }}
    >
      <option value="">Select a material</option>
      {materials.map((material) => (
        <option key={material.material_id} value={material.material_id}>
          {material.material_name} ({material.material_code})
        </option>
      ))}
    </select>
  )
}

function ReceiptForm({ materials }: { materials: MaterialStatus[] }) {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ReceiptValues>({
    resolver: zodResolver(receiptSchema),
    defaultValues: { material_id: '', lot_code: '', quantity: 0, accept: false },
  })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: ReceiptValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/stock/receipts', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          body: values,
        }),
      ),
    onSuccess: async (result) => {
      idempotency.reset()
      showToast(`Received ${result.movement.quantity} into lot ${result.lot.lot_code}.`)
      reset({ material_id: '', lot_code: '', quantity: 0, accept: false })
      await queryClient.invalidateQueries({ queryKey: ['materials', factory.id] })
      await queryClient.invalidateQueries({ queryKey: ['material-ledger', factory.id] })
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  return (
    <form noValidate className="flex flex-col gap-5" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The receipt was not recorded" error={mutation.error} />}
      <FormField id="receipt-material" label="Material" required error={errors.material_id?.message}>
        <select className="input" {...fieldAria('receipt-material', errors.material_id?.message, undefined, true)} {...register('material_id')}>
          <option value="">Select a material</option>
          {materials.map((material) => (
            <option key={material.material_id} value={material.material_id}>
              {material.material_name} ({material.material_code})
            </option>
          ))}
        </select>
      </FormField>
      <FormField id="receipt-lot-code" label="Lot code" required error={errors.lot_code?.message}>
        <input className="input font-mono" {...fieldAria('receipt-lot-code', errors.lot_code?.message, undefined, true)} {...register('lot_code')} />
      </FormField>
      <FormField id="receipt-quantity" label="Quantity" required error={errors.quantity?.message}>
        <input
          type="number"
          step="0.0001"
          className="input tabular-nums"
          {...fieldAria('receipt-quantity', errors.quantity?.message, undefined, true)}
          {...register('quantity', { valueAsNumber: true })}
        />
      </FormField>
      <label className="flex items-center gap-2">
        <input type="checkbox" className="h-4 w-4" {...register('accept')} />
        Accept this lot now
      </label>
      <div>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Recording…' : 'Record receipt'}
        </button>
      </div>
    </form>
  )
}

function AcceptForm({ materials }: { materials: MaterialStatus[] }) {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const [materialId, setMaterialId] = useState('')
  const lots = useMaterialLots(factory.id, materialId)
  const {
    register,
    handleSubmit,
    setValue,
    reset,
    formState: { errors },
  } = useForm<AcceptValues>({ resolver: zodResolver(acceptSchema), defaultValues: { material_id: '', lot_id: '' } })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: AcceptValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/stock/lots/{lot_id}/accept', {
          params: { path: { factory_id: factory.id, lot_id: values.lot_id }, header: { 'Idempotency-Key': key } },
        }),
      ),
    onSuccess: async (result) => {
      idempotency.reset()
      showToast(`Lot ${result.lot.lot_code} accepted.`)
      reset({ material_id: '', lot_id: '' })
      setMaterialId('')
      await queryClient.invalidateQueries({ queryKey: ['materials', factory.id] })
      await queryClient.invalidateQueries({ queryKey: ['material-ledger', factory.id] })
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  return (
    <form noValidate className="flex flex-col gap-5" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The lot was not accepted" error={mutation.error} />}
      <FormField id="accept-material" label="Material" required error={errors.material_id?.message}>
        <MaterialSelect
          id="accept-material"
          materials={materials}
          value={materialId}
          onChange={(value) => {
            setMaterialId(value)
            setValue('material_id', value)
            setValue('lot_id', '')
          }}
        />
      </FormField>
      <FormField
        id="accept-lot"
        label="Lot"
        required
        error={errors.lot_id?.message}
        hint="Lots seen in this material's ledger. Newly received lots appear here once recorded."
      >
        <select
          className="input"
          disabled={!materialId || lots.isPending}
          {...fieldAria('accept-lot', errors.lot_id?.message, 'hint', true)}
          {...register('lot_id')}
        >
          <option value="">{materialId ? (lots.isPending ? 'Loading lots…' : 'Select a lot') : 'Select a material first'}</option>
          {lots.data?.map((lot) => (
            <option key={lot.id} value={lot.id}>
              {lot.code}
            </option>
          ))}
        </select>
      </FormField>
      <div>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Accepting…' : 'Accept lot'}
        </button>
      </div>
    </form>
  )
}

function IssueForm({ materials }: { materials: MaterialStatus[] }) {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const [materialId, setMaterialId] = useState('')
  const lots = useMaterialLots(factory.id, materialId)
  const {
    register,
    handleSubmit,
    setValue,
    reset,
    formState: { errors },
  } = useForm<IssueValues>({
    resolver: zodResolver(issueSchema),
    defaultValues: { material_id: '', lot_id: '', quantity: 0, order_id: '', reason: '' },
  })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: IssueValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/stock/issues', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          body: {
            material_id: values.material_id,
            lot_id: values.lot_id,
            quantity: values.quantity,
            order_id: values.order_id || null,
            reason: values.reason || null,
          },
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      showToast('Issue recorded.')
      reset({ material_id: '', lot_id: '', quantity: 0, order_id: '', reason: '' })
      setMaterialId('')
      await queryClient.invalidateQueries({ queryKey: ['materials', factory.id] })
      await queryClient.invalidateQueries({ queryKey: ['material-ledger', factory.id] })
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  return (
    <form noValidate className="flex flex-col gap-5" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The issue was not recorded" error={mutation.error} />}
      <FormField id="issue-material" label="Material" required error={errors.material_id?.message}>
        <MaterialSelect
          id="issue-material"
          materials={materials}
          value={materialId}
          onChange={(value) => {
            setMaterialId(value)
            setValue('material_id', value)
            setValue('lot_id', '')
          }}
        />
      </FormField>
      <FormField id="issue-lot" label="Lot" required error={errors.lot_id?.message}>
        <select className="input" disabled={!materialId || lots.isPending} {...fieldAria('issue-lot', errors.lot_id?.message, undefined, true)} {...register('lot_id')}>
          <option value="">{materialId ? (lots.isPending ? 'Loading lots…' : 'Select a lot') : 'Select a material first'}</option>
          {lots.data?.map((lot) => (
            <option key={lot.id} value={lot.id}>
              {lot.code}
            </option>
          ))}
        </select>
      </FormField>
      <FormField id="issue-quantity" label="Quantity" required error={errors.quantity?.message}>
        <input
          type="number"
          step="0.0001"
          className="input tabular-nums"
          {...fieldAria('issue-quantity', errors.quantity?.message, undefined, true)}
          {...register('quantity', { valueAsNumber: true })}
        />
      </FormField>
      <FormField id="issue-order" label="Order (optional)" hint="Order id, if this issue is for a specific order.">
        <input className="input font-mono" {...fieldAria('issue-order', undefined, 'hint')} {...register('order_id')} />
      </FormField>
      <FormField id="issue-reason" label="Reason (optional)" error={errors.reason?.message}>
        <input className="input" {...fieldAria('issue-reason', errors.reason?.message)} {...register('reason')} />
      </FormField>
      <div>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Recording…' : 'Record issue'}
        </button>
      </div>
    </form>
  )
}

function CorrectionForm() {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<CorrectionValues>({
    resolver: zodResolver(correctionSchema),
    defaultValues: { movement_id: '', quantity_delta: 0, reason: '' },
  })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: CorrectionValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/stock/corrections', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          body: values,
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      showToast('Correction recorded.')
      reset({ movement_id: '', quantity_delta: 0, reason: '' })
      await queryClient.invalidateQueries({ queryKey: ['materials', factory.id] })
      await queryClient.invalidateQueries({ queryKey: ['material-ledger', factory.id] })
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  return (
    <form noValidate className="flex flex-col gap-5" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The correction was not recorded" error={mutation.error} />}
      <FormField
        id="correction-movement"
        label="Movement id"
        required
        error={errors.movement_id?.message}
        hint="Copy the movement id from the ledger row being corrected."
      >
        <input className="input font-mono" {...fieldAria('correction-movement', errors.movement_id?.message, 'hint', true)} {...register('movement_id')} />
      </FormField>
      <FormField id="correction-delta" label="Quantity adjustment" required error={errors.quantity_delta?.message}>
        <input
          type="number"
          step="0.0001"
          className="input tabular-nums"
          {...fieldAria('correction-delta', errors.quantity_delta?.message, undefined, true)}
          {...register('quantity_delta', { valueAsNumber: true })}
        />
      </FormField>
      <FormField id="correction-reason" label="Reason" required error={errors.reason?.message}>
        <input className="input" {...fieldAria('correction-reason', errors.reason?.message, undefined, true)} {...register('reason')} />
      </FormField>
      <div>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Recording…' : 'Record correction'}
        </button>
      </div>
    </form>
  )
}

/**
 * Storekeeper-only stock movement forms (`inventory:write`): receipt, accept
 * lot, issue and correction. Every write requires an `Idempotency-Key` (one
 * per submission attempt, see `useIdempotencyKey`) and the UI only updates
 * after the server confirms — no optimistic updates. A 409 from the server
 * (e.g. a stale correction target) is shown inline via `ErrorState`.
 */
export function StockMovementForms({ materials }: { materials: MaterialStatus[] }) {
  return (
    <Tabs
      label="Stock movement type"
      tabs={[
        { id: 'receipt', label: 'Receipt', content: <ReceiptForm materials={materials} /> },
        { id: 'accept', label: 'Accept lot', content: <AcceptForm materials={materials} /> },
        { id: 'issue', label: 'Issue', content: <IssueForm materials={materials} /> },
        { id: 'correction', label: 'Correction', content: <CorrectionForm /> },
      ]}
    />
  )
}
