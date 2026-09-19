import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useFieldArray, useForm } from 'react-hook-form'
import { z } from 'zod'

import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { fieldAria } from '../../components/fieldAria'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'

const SEVERITIES = ['MINOR', 'MAJOR', 'CRITICAL'] as const

const defectSchema = z.object({
  defect_code: z.string().min(1, 'Enter a defect code.').max(64, 'Keep the code under 64 characters.'),
  severity: z.enum(SEVERITIES, { error: 'Select a severity.' }),
  count: z.number({ error: 'Enter a count of 1 or more.' }).int('Enter a whole number.').min(1, 'Enter a count of 1 or more.'),
})

const inspectionSchema = z
  .object({
    inspection_type: z.enum(['INLINE', 'FINAL'], { error: 'Select an inspection type.' }),
    inspected_units: z.number({ error: 'Enter the inspected unit count.' }).int('Enter a whole number.').min(0, 'Enter 0 or more.'),
    defective_units: z.number({ error: 'Enter the defective unit count.' }).int('Enter a whole number.').min(0, 'Enter 0 or more.'),
    defects: z.array(defectSchema),
  })
  .refine((data) => data.defective_units <= data.inspected_units, {
    message: 'Defective units cannot exceed inspected units.',
    path: ['defective_units'],
  })
type InspectionValues = z.infer<typeof inspectionSchema>

const RESULT_LABEL: Record<string, { label: string; icon: 'check-circle' | 'x-circle' | 'question' }> = {
  PASS: { label: 'Pass', icon: 'check-circle' },
  FAIL: { label: 'Fail', icon: 'x-circle' },
  INSUFFICIENT_SAMPLE: { label: 'Insufficient sample', icon: 'question' },
}

/**
 * Records an inspection (`quality:inspect`) and shows the deterministic
 * result the server computed (PASS, FAIL or INSUFFICIENT_SAMPLE) — never a
 * client-side guess. Client validation mirrors the server's
 * `defective_units <= inspected_units` check but the server is the
 * authority.
 */
export function InspectionForm({
  orderId,
  onRecorded,
}: {
  orderId: string
  onRecorded: (result: Schemas['OrderQualityOut']) => void
}) {
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const queryClient = useQueryClient()

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<InspectionValues>({
    resolver: zodResolver(inspectionSchema),
    defaultValues: { inspection_type: 'INLINE', inspected_units: 0, defective_units: 0, defects: [] },
  })
  const { fields, append, remove } = useFieldArray({ control, name: 'defects' })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: InspectionValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/orders/{order_id}/inspections', {
          params: { path: { order_id: orderId }, header: { 'Idempotency-Key': key } },
          body: {
            inspection_type: values.inspection_type,
            inspected_units: values.inspected_units,
            defective_units: values.defective_units,
            defects: values.defects,
            line_id: null,
          },
        }),
      ),
    onSuccess: (result) => {
      idempotency.reset()
      const latest = result.inspections[0]
      showToast(latest ? `Inspection recorded: ${RESULT_LABEL[latest.result]?.label ?? latest.result}.` : 'Inspection recorded.')
      reset({ inspection_type: 'INLINE', inspected_units: 0, defective_units: 0, defects: [] })
      queryClient.setQueryData(['order-quality', orderId], result)
      onRecorded(result)
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  const latestResult = mutation.data?.inspections[0]?.result

  return (
    <form noValidate className="flex flex-col gap-5" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The inspection was not recorded" error={mutation.error} />}
      {latestResult && (
        <div className="flex items-center gap-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm font-medium">
          <Icon name={RESULT_LABEL[latestResult]?.icon ?? 'question'} />
          Result: {RESULT_LABEL[latestResult]?.label ?? latestResult}
        </div>
      )}
      <div className="grid gap-5 md:grid-cols-3">
        <FormField id="inspection-type" label="Inspection type" required error={errors.inspection_type?.message}>
          <select className="input" {...fieldAria('inspection-type', errors.inspection_type?.message, undefined, true)} {...register('inspection_type')}>
            <option value="INLINE">Inline</option>
            <option value="FINAL">Final</option>
          </select>
        </FormField>
        <FormField id="inspected-units" label="Inspected units" required error={errors.inspected_units?.message}>
          <input
            type="number"
            min={0}
            step={1}
            className="input tabular-nums"
            {...fieldAria('inspected-units', errors.inspected_units?.message, undefined, true)}
            {...register('inspected_units', { valueAsNumber: true })}
          />
        </FormField>
        <FormField id="defective-units" label="Defective units" required error={errors.defective_units?.message}>
          <input
            type="number"
            min={0}
            step={1}
            className="input tabular-nums"
            {...fieldAria('defective-units', errors.defective_units?.message, undefined, true)}
            {...register('defective_units', { valueAsNumber: true })}
          />
        </FormField>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Defects</h3>
          <button
            type="button"
            className="btn-secondary h-8"
            onClick={() => {
              append({ defect_code: '', severity: 'MINOR', count: 1 })
            }}
          >
            <Icon name="plus" />
            Add defect
          </button>
        </div>
        {fields.map((field, index) => (
          <div key={field.id} className="grid items-end gap-3 border-t border-line pt-3 md:grid-cols-[2fr_1fr_1fr_auto]">
            <FormField
              id={`defect-code-${index}`}
              label="Catalog code"
              required
              error={errors.defects?.[index]?.defect_code?.message}
            >
              <input
                className="input font-mono"
                {...fieldAria(`defect-code-${index}`, errors.defects?.[index]?.defect_code?.message, undefined, true)}
                {...register(`defects.${index}.defect_code`)}
              />
            </FormField>
            <FormField id={`defect-severity-${index}`} label="Severity" required error={errors.defects?.[index]?.severity?.message}>
              <select
                className="input"
                {...fieldAria(`defect-severity-${index}`, errors.defects?.[index]?.severity?.message, undefined, true)}
                {...register(`defects.${index}.severity`)}
              >
                {SEVERITIES.map((severity) => (
                  <option key={severity} value={severity}>
                    {severity}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField id={`defect-count-${index}`} label="Count" required error={errors.defects?.[index]?.count?.message}>
              <input
                type="number"
                min={1}
                step={1}
                className="input tabular-nums"
                {...fieldAria(`defect-count-${index}`, errors.defects?.[index]?.count?.message, undefined, true)}
                {...register(`defects.${index}.count`, { valueAsNumber: true })}
              />
            </FormField>
            <button
              type="button"
              className="btn-secondary h-9"
              onClick={() => {
                remove(index)
              }}
              aria-label={`Remove defect row ${index + 1}`}
            >
              <Icon name="x" />
            </button>
          </div>
        ))}
      </div>

      <div>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Recording…' : 'Record inspection'}
        </button>
      </div>
    </form>
  )
}
