import { useQuery } from '@tanstack/react-query'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime, humanizeCode } from '../../lib/format'

type HistoryEvent = Schemas['OrderHistoryEventOut']

function columns(timeZone: string): Column<HistoryEvent>[] {
  return [
    { key: 'created_at', header: 'When', render: (row) => formatDateTime(row.created_at, timeZone) },
    { key: 'actor', header: 'Who', render: (row) => row.actor_display_name },
    { key: 'action', header: 'Action', render: (row) => humanizeCode(row.action.replace(/\./g, ' ')) },
    { key: 'outcome', header: 'Outcome', render: (row) => row.outcome },
    { key: 'reason', header: 'Reason', render: (row) => row.reason ?? '' },
  ]
}

/** The order's audit history (`GET /orders/{id}/history`, `order:read`). */
export function OrderHistoryTab({ orderId }: { orderId: string }) {
  const factory = useFactory()
  const query = useQuery({
    queryKey: ['order-history', orderId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/orders/{order_id}/history', {
          params: { path: { order_id: orderId }, query: { limit: 50, offset: 0 } },
        }),
      ),
  })

  if (query.isPending) return <LoadingState label="Loading history…" variant="table" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data.items.length === 0) {
    return <EmptyState icon="info" title="No history yet" description="Actions taken on this order will appear here." />
  }
  return (
    <DataTable
      caption="Order history"
      columns={columns(factory.timezone)}
      rows={query.data.items}
      rowKey={(row) => String(row.id)}
    />
  )
}
