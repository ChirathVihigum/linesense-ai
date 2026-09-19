import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { Link, useNavigate } from 'react-router'

import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { fieldAria } from '../../components/fieldAria'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { ApiError, api, fetchAllPages, unwrap } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'
import {
  ORDER_FORM_FIELDS,
  orderCreateSchema,
  orderCreatedPath,
  type OrderCreateValues,
} from './orderCreateSchema'

function useReferenceOptions(factoryId: string) {
  const customers = useQuery({
    queryKey: ['customers', factoryId],
    queryFn: () =>
      fetchAllPages((offset, limit) =>
        unwrap(
          api.GET('/api/v1/factories/{factory_id}/customers', {
            params: { path: { factory_id: factoryId }, query: { offset, limit } },
          }),
        ),
      ),
    staleTime: 5 * 60_000,
  })
  const styles = useQuery({
    queryKey: ['styles', factoryId],
    queryFn: () =>
      fetchAllPages((offset, limit) =>
        unwrap(
          api.GET('/api/v1/factories/{factory_id}/styles', {
            params: { path: { factory_id: factoryId }, query: { offset, limit } },
          }),
        ),
      ),
    staleTime: 5 * 60_000,
  })
  return { customers, styles }
}

function OrderCreateForm() {
  const factory = useFactory()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const { customers, styles } = useReferenceOptions(factory.id)

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<OrderCreateValues>({
    resolver: zodResolver(orderCreateSchema),
    defaultValues: { external_ref: '', customer_id: '', style_id: '', due_date: '', priority: 3 },
  })

  const mutation = useMutation({
    mutationFn: ({ values, key }: { values: OrderCreateValues; key: string }) =>
      unwrap(
        api.POST('/api/v1/factories/{factory_id}/orders', {
          params: { path: { factory_id: factory.id }, header: { 'Idempotency-Key': key } },
          body: values,
        }),
      ),
    onSuccess: async (order) => {
      idempotency.reset()
      showToast(`Order ${order.external_ref} created.`)
      await queryClient.invalidateQueries({ queryKey: ['orders', factory.id] })
      await navigate(orderCreatedPath(factory.code))
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) return
      for (const fieldError of error.fieldErrors) {
        if (ORDER_FORM_FIELDS.has(fieldError.field)) {
          setError(fieldError.field as keyof OrderCreateValues, {
            type: 'server',
            message: fieldError.message,
          })
        }
      }
    },
  })

  const onSubmit = handleSubmit((values) => {
    // Same values as the failed attempt → same key (a safe retry); changed values → new key.
    mutation.mutate({ values, key: idempotency.keyFor(values) })
  })

  const referenceError = customers.error ?? styles.error

  return (
    <form noValidate className="panel flex max-w-3xl flex-col gap-5 p-6" onSubmit={(event) => void onSubmit(event)}>
      {mutation.isError && <ErrorState title="The order was not created" error={mutation.error} />}
      {referenceError && (
        <ErrorState
          title="Customers or styles could not be loaded"
          error={referenceError}
          onRetry={() => {
            void customers.refetch()
            void styles.refetch()
          }}
        />
      )}

      <FormField
        id="external_ref"
        label="External reference"
        hint="The customer's purchase order number, e.g. PO-1001."
        error={errors.external_ref?.message}
        required
      >
        <input
          className="input font-mono"
          autoComplete="off"
          {...fieldAria('external_ref', errors.external_ref?.message, 'hint', true)}
          {...register('external_ref')}
        />
      </FormField>

      <div className="grid gap-5 md:grid-cols-2">
        <FormField id="customer_id" label="Customer" error={errors.customer_id?.message} required>
          <select
            className="input"
            disabled={customers.isPending}
            {...fieldAria('customer_id', errors.customer_id?.message, undefined, true)}
            {...register('customer_id')}
          >
            <option value="">{customers.isPending ? 'Loading customers…' : 'Select a customer'}</option>
            {customers.data?.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.name} ({customer.code})
              </option>
            ))}
          </select>
        </FormField>
        <FormField id="style_id" label="Style" error={errors.style_id?.message} required>
          <select
            className="input"
            disabled={styles.isPending}
            {...fieldAria('style_id', errors.style_id?.message, undefined, true)}
            {...register('style_id')}
          >
            <option value="">{styles.isPending ? 'Loading styles…' : 'Select a style'}</option>
            {styles.data?.map((style) => (
              <option key={style.id} value={style.id}>
                {style.name} ({style.code})
              </option>
            ))}
          </select>
        </FormField>
      </div>

      <div className="grid gap-5 md:grid-cols-3">
        <FormField id="quantity" label="Quantity (units)" error={errors.quantity?.message} required>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={1_000_000}
            step={1}
            className="input tabular-nums"
            {...fieldAria('quantity', errors.quantity?.message, undefined, true)}
            {...register('quantity', { valueAsNumber: true })}
          />
        </FormField>
        <FormField id="due_date" label="Due date" error={errors.due_date?.message} required>
          <input
            type="date"
            className="input"
            {...fieldAria('due_date', errors.due_date?.message, undefined, true)}
            {...register('due_date')}
          />
        </FormField>
        <FormField
          id="priority"
          label="Priority"
          hint="1 is the highest."
          error={errors.priority?.message}
          required
        >
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={5}
            step={1}
            className="input tabular-nums"
            {...fieldAria('priority', errors.priority?.message, 'hint', true)}
            {...register('priority', { valueAsNumber: true })}
          />
        </FormField>
      </div>

      <div className="flex items-center gap-3 border-t border-line pt-5">
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Creating…' : 'Create order'}
        </button>
        <Link className="btn-secondary" to={`/f/${encodeURIComponent(factory.code)}/orders`}>
          Cancel
        </Link>
      </div>
    </form>
  )
}

export function OrderCreatePage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="New order"
        description="Orders start as drafts. Materials and capacity are checked once the order is validated."
      />
      {can('order:create') ? (
        <OrderCreateForm />
      ) : (
        <PermissionDenied message="Only planners and supervisors can create orders." />
      )}
    </>
  )
}
