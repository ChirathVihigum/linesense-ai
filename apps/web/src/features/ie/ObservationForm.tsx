import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { fieldAria } from '../../components/fieldAria'
import { api, fetchAllPages, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime, formatDecimal } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'

type Operation = Schemas['OperationAnalysisOut']
type Observation = Schemas['ObservationOut']

const observationSchema = z.object({
  operation_id: z.string().min(1, 'Select an operation.'),
  operator_alias_code: z.string().min(1, 'Select an operator alias.'),
  observed_seconds: z
    .number({ error: 'Enter a cycle time greater than 0 seconds.' })
    .positive('Enter a cycle time greater than 0 seconds.')
    .max(3600, 'Enter a value of 3600 seconds or less.'),
  observed_at: z.string().min(1, 'Enter when this was observed.'),
})
type ObservationValues = z.infer<typeof observationSchema>

function useOperatorAliases(factoryId: string) {
  return useQuery({
    queryKey: ['operator-aliases', factoryId],
    queryFn: () =>
      fetchAllPages<Schemas['OperatorAliasOut']>((offset, limit) =>
        unwrap(
          api.GET('/api/v1/factories/{factory_id}/ie/operator-aliases', {
            params: { path: { factory_id: factoryId }, query: { offset, limit } },
          }),
        ),
      ),
    staleTime: 60_000,
  })
}

function OutlierAction({
  observation,
  onMarked,
}: {
  observation: Observation
  onMarked: (updated: Observation) => void
}) {
  const [reason, setReason] = useState('')
  const [open, setOpen] = useState(false)
  const idempotency = useIdempotencyKey()
  const queryClient = useQueryClient()
  const factory = useFactory()

  const trimmedReason = reason.trim()
  const reasonMissing = trimmedReason === ''
  const reasonErrorId = `outlier-reason-error-${observation.id}`

  const mutation = useMutation({
    mutationFn: ({ key, reason: reasonToSend }: { key: string; reason: string }) =>
      unwrap(
        api.POST('/api/v1/ie/observations/{observation_id}/outlier', {
          params: { path: { observation_id: observation.id }, header: { 'Idempotency-Key': key } },
          body: { reason: reasonToSend },
        }),
      ),
    onSuccess: async (updated) => {
      idempotency.reset()
      setOpen(false)
      // The server's response is the source of truth for `is_outlier`; the parent's
      // session-local list is updated from it (no optimistic update).
      onMarked(updated)
      await queryClient.invalidateQueries({ queryKey: ['ie-analysis', factory.id] })
    },
  })

  if (observation.is_outlier) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-warn-fg">
        <Icon name="alert" className="h-3.5 w-3.5" />
        Marked as outlier
      </span>
    )
  }

  if (!open) {
    return (
      <button
        type="button"
        className="link text-xs"
        onClick={() => {
          setOpen(true)
        }}
      >
        Mark as outlier
      </button>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor={`outlier-reason-${observation.id}`}>
        Reason for marking this observation an outlier
      </label>
      <input
        id={`outlier-reason-${observation.id}`}
        className="input h-8 w-48"
        placeholder="Reason"
        value={reason}
        required
        aria-required="true"
        aria-invalid={reasonMissing || undefined}
        aria-describedby={reasonMissing ? reasonErrorId : undefined}
        onChange={(event) => {
          setReason(event.target.value)
        }}
      />
      <button
        type="button"
        className="btn-secondary h-8"
        disabled={mutation.isPending || reasonMissing}
        onClick={() => {
          if (reasonMissing) return
          mutation.mutate({
            key: idempotency.keyFor({ observation_id: observation.id, reason: trimmedReason }),
            reason: trimmedReason,
          })
        }}
      >
        {mutation.isPending ? 'Marking…' : 'Confirm'}
      </button>
      {reasonMissing && (
        <p id={reasonErrorId} className="w-full text-xs font-medium text-bad-fg">
          Enter a reason for marking this observation an outlier.
        </p>
      )}
      {mutation.isError && <ErrorState title="Could not mark as outlier" error={mutation.error} />}
    </div>
  )
}

/**
 * Records a cycle observation for the selected line/style. The operator
 * selector lists only pseudonymous alias codes (`OperatorAliasOut` has no
 * name field anywhere in the contract) — never a person's name. Newly
 * recorded observations appear below with an inline "mark as outlier"
 * action (with a required reason), since the API has no endpoint to list
 * historical observations to mark.
 */
export function ObservationForm({
  factoryId,
  lineId,
  styleId,
  operations,
}: {
  factoryId: string
  lineId: string
  styleId: string
  operations: Operation[]
}) {
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const queryClient = useQueryClient()
  const aliases = useOperatorAliases(factoryId)
  const [recorded, setRecorded] = useState<Observation[]>([])

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ObservationValues>({
    resolver: zodResolver(observationSchema),
    defaultValues: { operation_id: '', operator_alias_code: '', observed_seconds: 0, observed_at: '' },
  })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: ObservationValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/ie/observations', {
          params: { path: { factory_id: factoryId }, header: { 'Idempotency-Key': key } },
          body: {
            line_id: lineId,
            style_id: styleId,
            operation_id: values.operation_id,
            operator_alias_code: values.operator_alias_code,
            observed_seconds: values.observed_seconds,
            observed_at: new Date(values.observed_at).toISOString(),
          },
        }),
      ),
    onSuccess: async (observation) => {
      idempotency.reset()
      showToast('Observation recorded.')
      setRecorded((current) => [observation, ...current])
      reset({ operation_id: '', operator_alias_code: '', observed_seconds: 0, observed_at: '' })
      await queryClient.invalidateQueries({ queryKey: ['ie-analysis', factoryId] })
    },
  })

  const onSubmit = handleSubmit((values) => {
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  return (
    <div className="flex flex-col gap-5">
      <form noValidate className="grid gap-5 md:grid-cols-2" onSubmit={(event) => void onSubmit(event)}>
        {mutation.isError && (
          <div className="md:col-span-2">
            <ErrorState title="The observation was not recorded" error={mutation.error} />
          </div>
        )}
        <FormField id="observation-operation" label="Operation" required error={errors.operation_id?.message}>
          <select className="input" {...fieldAria('observation-operation', errors.operation_id?.message, undefined, true)} {...register('operation_id')}>
            <option value="">Select an operation</option>
            {operations.map((operation) => (
              <option key={operation.operation_id} value={operation.operation_id}>
                {operation.code} {operation.name}
              </option>
            ))}
          </select>
        </FormField>
        <FormField
          id="observation-alias"
          label="Operator alias"
          required
          error={errors.operator_alias_code?.message}
          hint="Pseudonymous alias codes only. No operator names are stored or shown."
        >
          <select
            className="input"
            disabled={aliases.isPending}
            {...fieldAria('observation-alias', errors.operator_alias_code?.message, 'hint', true)}
            {...register('operator_alias_code')}
          >
            <option value="">{aliases.isPending ? 'Loading aliases…' : 'Select an alias'}</option>
            {aliases.data
              ?.filter((alias) => alias.is_active)
              .map((alias) => (
                <option key={alias.id} value={alias.alias_code}>
                  {alias.alias_code}
                </option>
              ))}
          </select>
        </FormField>
        <FormField id="observation-seconds" label="Observed cycle time (seconds)" required error={errors.observed_seconds?.message}>
          <input
            type="number"
            step="0.01"
            className="input tabular-nums"
            {...fieldAria('observation-seconds', errors.observed_seconds?.message, undefined, true)}
            {...register('observed_seconds', { valueAsNumber: true })}
          />
        </FormField>
        <FormField id="observation-at" label="Observed at" required error={errors.observed_at?.message}>
          <input
            type="datetime-local"
            className="input"
            {...fieldAria('observation-at', errors.observed_at?.message, undefined, true)}
            {...register('observed_at')}
          />
        </FormField>
        <div className="md:col-span-2">
          <button type="submit" className="btn-primary" disabled={mutation.isPending}>
            {mutation.isPending ? 'Recording…' : 'Record observation'}
          </button>
        </div>
      </form>
      {recorded.length > 0 && (
        <ul aria-label="Recorded this session" className="flex flex-col gap-2">
          {recorded.map((observation) => (
            <li key={observation.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2 text-sm">
              <span>
                {formatDecimal(observation.observed_seconds, 2)} s at{' '}
                {formatDateTime(observation.observed_at, 'UTC')}
              </span>
              <OutlierAction
                observation={observation}
                onMarked={(updated) => {
                  setRecorded((current) => current.map((item) => (item.id === updated.id ? updated : item)))
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
