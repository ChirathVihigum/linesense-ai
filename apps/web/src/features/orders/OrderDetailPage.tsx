import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { StaleBanner } from '../../components/StaleBanner'
import { StateBadge } from '../../components/StateBadge'
import { fieldAria } from '../../components/fieldAria'
import { Tabs, type TabItem } from '../../components/Tabs'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDate, formatInteger } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'
import { isoDateInTimeZone } from '../planning/dateRange'
import { OrderEvidenceTab } from './OrderEvidenceTab'
import { OrderHistoryTab } from './OrderHistoryTab'
import { OrderIETab } from './OrderIETab'
import { OrderMaterialsTab } from './OrderMaterialsTab'
import { OrderOverviewTab } from './OrderOverviewTab'
import { OrderPlanTab } from './OrderPlanTab'
import { OrderQualityTab } from './OrderQualityTab'
import { OrderRunsTab } from './OrderRunsTab'
import { StartAnalysisButton } from './StartAnalysisButton'

function daysRemaining(dueDate: string, timeZone: string): number {
  const today = isoDateInTimeZone(timeZone)
  const todayMs = new Date(`${today}T00:00:00Z`).getTime()
  const dueMs = new Date(`${dueDate}T00:00:00Z`).getTime()
  return Math.round((dueMs - todayMs) / 86_400_000)
}

function ShipmentBadge({ shipment }: { shipment: Schemas['OrderDetail']['shipment'] }) {
  return (
    <div className="flex flex-col gap-1">
      <span
        className={
          shipment.eligible
            ? 'inline-flex h-6 w-fit items-center gap-1 rounded-full border border-ok-line bg-ok-bg px-2 text-xs font-medium text-ok-fg'
            : 'inline-flex h-6 w-fit items-center gap-1 rounded-full border border-neutral-line bg-neutral-bg px-2 text-xs font-medium text-neutral-fg'
        }
      >
        <Icon name={shipment.eligible ? 'truck' : 'ban'} className="h-3.5 w-3.5" />
        {shipment.eligible ? 'Eligible for shipment' : 'Not eligible for shipment'}
      </span>
      {shipment.reasons.length > 0 && (
        <ul className="text-xs text-fg-muted">
          {shipment.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function OrderDetailView({ orderId }: { orderId: string }) {
  const factory = useFactory()
  const can = useCan()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const [transitionTarget, setTransitionTarget] = useState<string | null>(null)
  const [transitionReason, setTransitionReason] = useState('')

  const query = useQuery({
    queryKey: ['order', orderId],
    queryFn: () => unwrap(api.GET('/api/v1/orders/{order_id}', { params: { path: { order_id: orderId } } })),
  })

  const transitionMutation = useMutation({
    mutationFn: ({ key, target, version }: { key: string; target: string; version: number }) =>
      unwrap(
        api.POST('/api/v1/orders/{order_id}/transitions', {
          params: { path: { order_id: orderId }, header: { 'Idempotency-Key': key } },
          body: { target_state: target, expected_version: version, reason: transitionReason.trim() || null },
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      setTransitionTarget(null)
      setTransitionReason('')
      showToast('Order updated.')
      await queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    },
  })

  if (query.isPending) return <LoadingState label="Loading order…" variant="page" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }
  const order = query.data
  const days = daysRemaining(order.due_date, factory.timezone)
  const isStaleTransition = transitionMutation.error instanceof ApiError && transitionMutation.error.code === 'STALE_INPUT'

  const tabs: TabItem[] = [
    { id: 'overview', label: 'Overview', content: <OrderOverviewTab order={order} /> },
    { id: 'plan', label: 'Plan', content: <OrderPlanTab order={order} /> },
    { id: 'materials', label: 'Materials', content: <OrderMaterialsTab order={order} /> },
    { id: 'ie', label: 'IE', content: <OrderIETab order={order} /> },
    { id: 'quality', label: 'Quality', content: <OrderQualityTab order={order} /> },
    { id: 'evidence', label: 'Evidence', content: <OrderEvidenceTab order={order} /> },
    { id: 'runs', label: 'Runs', content: <OrderRunsTab orderId={orderId} /> },
    { id: 'history', label: 'History', content: <OrderHistoryTab orderId={orderId} /> },
  ]

  return (
    <>
      <PageHeader
        title={order.external_ref}
        description={`${order.customer.name} · ${order.style.name}`}
        actions={can('analysis:run') ? <StartAnalysisButton order={order} /> : undefined}
      />

      <div className="panel mb-6 flex flex-col gap-4 p-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <dt className="text-xs font-medium text-fg-muted">Quantity</dt>
          <dd className="tabular-nums">{formatInteger(order.quantity)}</dd>
          <dt className="text-xs font-medium text-fg-muted">Produced / packed</dt>
          <dd className="tabular-nums">
            {formatInteger(order.produced_units)} / {formatInteger(order.packed_units)}
          </dd>
          <dt className="text-xs font-medium text-fg-muted">Due date</dt>
          <dd>
            {formatDate(order.due_date)}{' '}
            <span className="text-fg-muted">
              ({days >= 0 ? `${formatInteger(days)} days left` : `${formatInteger(Math.abs(days))} days overdue`})
            </span>
          </dd>
          <dt className="text-xs font-medium text-fg-muted">Priority</dt>
          <dd className="tabular-nums">{formatInteger(order.priority)}</dd>
        </dl>

        <div className="flex flex-wrap items-center gap-2">
          <StateBadge vocabulary="production" state={order.production_state} />
          <StateBadge vocabulary="material" state={order.material_state} />
          <StateBadge vocabulary="quality" state={order.quality_state} />
        </div>

        <ShipmentBadge shipment={order.shipment} />

        {can('order:transition') && order.allowed_transitions.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t border-line pt-3">
            {order.allowed_transitions.map((target) => (
              <button
                key={target}
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setTransitionReason('')
                  setTransitionTarget(target)
                }}
              >
                Move to {target.toLowerCase().replace(/_/g, ' ')}
              </button>
            ))}
          </div>
        )}

        {isStaleTransition && (
          <StaleBanner
            message="This order has changed."
            onRefresh={() => {
              transitionMutation.reset()
              void query.refetch()
            }}
          />
        )}
        {transitionMutation.isError && !isStaleTransition && (
          <ErrorState title="The order was not updated" error={transitionMutation.error} />
        )}
      </div>

      <Tabs label="Order sections" tabs={tabs} />

      <ConfirmDialog
        open={transitionTarget !== null}
        title={`Move order to ${transitionTarget?.toLowerCase().replace(/_/g, ' ')}?`}
        confirmLabel="Confirm"
        pending={transitionMutation.isPending}
        onConfirm={() => {
          if (transitionTarget) {
            transitionMutation.mutate({
              key: idempotency.keyFor({ orderId, target: transitionTarget, version: order.version }),
              target: transitionTarget,
              version: order.version,
            })
          }
        }}
        onCancel={() => {
          setTransitionTarget(null)
        }}
      >
        <FormField id="transition-reason" label="Reason" hint="Optional, up to 500 characters.">
          <textarea
            className="input"
            rows={3}
            maxLength={500}
            value={transitionReason}
            {...fieldAria('transition-reason', undefined, 'transition-reason-hint')}
            onChange={(event) => {
              setTransitionReason(event.target.value)
            }}
          />
        </FormField>
      </ConfirmDialog>
    </>
  )
}

export function OrderDetailPage() {
  const { orderId } = useParams<{ orderId: string }>()
  if (!orderId) return null
  return <OrderDetailView orderId={orderId} />
}
