import { useQuery } from '@tanstack/react-query'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { StateBadge } from '../../components/StateBadge'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime } from '../../lib/format'

type Hold = Schemas['QualityHoldOut']

function shortId(id: string): string {
  return id.slice(0, 8)
}

/** Every ACTIVE quality hold in the factory. Selecting a row loads that order in the review below. */
export function HoldsTable({ onSelectOrder }: { onSelectOrder: (orderId: string) => void }) {
  const factory = useFactory()
  const query = useQuery({
    queryKey: ['quality-holds', factory.id],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/quality/holds', {
          params: { path: { factory_id: factory.id }, query: { status: 'ACTIVE', limit: 50 } },
        }),
      ),
  })

  if (query.isPending) return <LoadingState label="Loading holds…" variant="table" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data.items.length === 0) {
    return <EmptyState icon="check-circle" title="No active holds" description="Orders on quality hold will appear here." />
  }

  const columns: Column<Hold>[] = [
    {
      key: 'order',
      header: 'Order',
      render: (row) => (
        <button
          type="button"
          className="link font-mono text-xs"
          onClick={() => {
            onSelectOrder(row.order_id)
          }}
          title={row.order_id}
        >
          Order id {shortId(row.order_id)}
        </button>
      ),
    },
    { key: 'reason', header: 'Reason', render: (row) => row.reason },
    { key: 'status', header: 'Status', render: () => <StateBadge vocabulary="quality" state="HOLD" /> },
    { key: 'created_at', header: 'Created', render: (row) => formatDateTime(row.created_at, factory.timezone) },
  ]

  return <DataTable caption="Active quality holds" columns={columns} rows={query.data.items} rowKey={(row) => row.id} />
}
