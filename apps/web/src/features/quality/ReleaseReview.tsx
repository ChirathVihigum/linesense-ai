import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useMe } from '../../lib/auth'
import { useFactory } from '../../lib/factory'
import { formatDateTime, formatInteger } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'

type OrderQuality = Schemas['OrderQualityOut']

/**
 * Reviews the order's latest FINAL inspection for release (`quality:release`).
 * Separation of duties is enforced by the server (403 `SELF_APPROVAL_DENIED`
 * when releaser == inspector); this also explains the rule up front and
 * disables the confirm action rather than let the viewer submit only to be
 * denied.
 */
export function ReleaseReview({ orderQuality }: { orderQuality: OrderQuality }) {
  const me = useMe()
  const factory = useFactory()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)

  const latestFinal = orderQuality.inspections.find((inspection) => inspection.inspection_type === 'FINAL') ?? null
  const isInspector = latestFinal?.inspected_by !== null && latestFinal?.inspected_by === me.user.id

  const mutation = useMutation({
    mutationFn: ({ key }: { key: string }) => {
      if (!latestFinal) throw new Error('no FINAL inspection to release')
      return unwrap(
        api.POST('/api/v1/orders/{order_id}/quality-release', {
          params: { path: { order_id: orderQuality.order_id }, header: { 'Idempotency-Key': key } },
          body: { inspection_id: latestFinal.id, expected_order_version: orderQuality.order_version },
        }),
      )
    },
    onSuccess: async (result) => {
      idempotency.reset()
      setConfirmOpen(false)
      showToast('Order released for shipment.')
      queryClient.setQueryData(['order-quality', orderQuality.order_id], result)
      await queryClient.invalidateQueries({ queryKey: ['orders', factory.id] })
    },
    onError: () => {
      setConfirmOpen(false)
    },
  })

  if (!latestFinal) {
    return <p className="text-fg-muted">No FINAL inspection has been recorded for this order yet.</p>
  }

  const canAttemptRelease = latestFinal.result === 'PASS' && !isInspector

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <dt className="text-fg-muted">Latest FINAL result</dt>
        <dd className="sm:col-span-3">{latestFinal.result}</dd>
        <dt className="text-fg-muted">Inspected</dt>
        <dd className="sm:col-span-3">
          {formatInteger(latestFinal.inspected_units)} units, {formatInteger(latestFinal.defective_units)} defective, at{' '}
          {formatDateTime(latestFinal.inspected_at, factory.timezone)}
        </dd>
        <dt className="text-fg-muted">Policy</dt>
        <dd className="flex flex-wrap items-center gap-2 sm:col-span-3">
          <span>
            {orderQuality.policy?.code} v{orderQuality.policy?.version_no}
          </span>
          {orderQuality.policy?.label && (
            <span className="inline-flex h-6 items-center gap-1 rounded-full border border-warn-line bg-warn-bg px-2 text-xs font-medium text-warn-fg">
              <Icon name="alert" className="h-3.5 w-3.5" />
              {orderQuality.policy.label}
            </span>
          )}
        </dd>
        <dt className="text-fg-muted">Shipment eligibility</dt>
        <dd className="sm:col-span-3">
          {orderQuality.shipment.eligible ? (
            <span className="inline-flex items-center gap-1 text-ok-fg">
              <Icon name="check-circle" className="h-4 w-4" />
              Eligible
            </span>
          ) : (
            <div>
              <span className="inline-flex items-center gap-1 text-bad-fg">
                <Icon name="ban" className="h-4 w-4" />
                Not eligible
              </span>
              {orderQuality.shipment.reasons.length > 0 && (
                <ul className="mt-1 list-disc pl-5 text-fg-muted">
                  {orderQuality.shipment.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </dd>
      </dl>

      {isInspector && (
        <p role="alert" className="flex items-start gap-2 rounded-md border border-warn-line bg-warn-bg px-3 py-2 text-warn-fg">
          <Icon name="lock" className="mt-0.5 h-4 w-4 shrink-0" />
          You inspected this order. Separation of duties requires a different person to release it.
        </p>
      )}
      {latestFinal.result !== 'PASS' && !isInspector && (
        <p className="text-fg-muted">Only a passing FINAL inspection can be released (current result: {latestFinal.result}).</p>
      )}

      {mutation.isError && <ErrorState title="The order was not released" error={mutation.error} />}

      <div>
        <button
          type="button"
          className="btn-primary"
          disabled={!canAttemptRelease}
          onClick={() => {
            setConfirmOpen(true)
          }}
        >
          Release for shipment
        </button>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Release this order for shipment?"
        confirmLabel={mutation.isPending ? 'Releasing…' : 'Release'}
        pending={mutation.isPending}
        onCancel={() => {
          setConfirmOpen(false)
        }}
        onConfirm={() => {
          mutation.mutate({ key: idempotency.keyFor({ order_id: orderQuality.order_id, version: orderQuality.order_version }) })
        }}
      >
        <p>This releases every active quality hold on the order and records who approved it.</p>
        {mutation.error instanceof ApiError && mutation.error.code === 'STALE_INPUT' && (
          <p className="mt-2 font-medium text-bad-fg">
            The order changed since it was loaded. Reload the order and try again.
          </p>
        )}
      </ConfirmDialog>
    </div>
  )
}
