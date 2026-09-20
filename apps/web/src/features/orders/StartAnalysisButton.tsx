import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'

import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { StaleBanner } from '../../components/StaleBanner'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { useIdempotencyKey } from '../../lib/idempotency'

const ACTIVE_RUN_STATUSES = new Set(['QUEUED', 'RUNNING', 'AWAITING_REVIEW'])

/** Requests an analysis run for the order. Disabled while a run is already active;
 * a 409 STALE_INPUT (the order changed) offers a reload, other errors (409 CONFLICT,
 * 429 RATE_LIMITED) show the server's message. */
export function StartAnalysisButton({ order }: { order: Schemas['OrderDetail'] }) {
  const factory = useFactory()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()

  const runActive = order.latest_run !== null && ACTIVE_RUN_STATUSES.has(order.latest_run.status)

  const mutation = useMutation({
    mutationFn: (key: string) =>
      unwrap(
        api.POST('/api/v1/orders/{order_id}/analyses', {
          params: { path: { order_id: order.id }, header: { 'Idempotency-Key': key } },
          body: { expected_order_version: order.version },
        }),
      ),
    onSuccess: async (accepted) => {
      idempotency.reset()
      await navigate(`/f/${encodeURIComponent(factory.code)}/runs/${accepted.run_id}`)
    },
  })

  const isStale = mutation.error instanceof ApiError && mutation.error.code === 'STALE_INPUT'

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        className="btn-primary"
        disabled={runActive || mutation.isPending}
        onClick={() => {
          mutation.mutate(idempotency.keyFor({ orderId: order.id, version: order.version }))
        }}
      >
        <Icon name="sparkle" />
        {mutation.isPending ? 'Starting…' : 'Start analysis'}
      </button>
      {mutation.isError &&
        (isStale ? (
          <StaleBanner
            message="This order has changed."
            onRefresh={() => {
              void queryClient.invalidateQueries({ queryKey: ['order', order.id] })
            }}
          />
        ) : (
          <ErrorState title="Analysis was not started" error={mutation.error} />
        ))}
    </div>
  )
}
