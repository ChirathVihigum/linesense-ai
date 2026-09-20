import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { StateBadge } from '../../components/StateBadge'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime } from '../../lib/format'

type RunSummary = Schemas['RunSummary']

function columns(factoryCode: string, timeZone: string): Column<RunSummary>[] {
  return [
    { key: 'status', header: 'Status', render: (row) => <StateBadge vocabulary="analysis" state={row.status} /> },
    { key: 'llm', header: 'LLM', render: (row) => row.llm.label },
    { key: 'requested_by', header: 'Requested by', render: (row) => row.requested_by.display_name },
    { key: 'created_at', header: 'Created', render: (row) => formatDateTime(row.created_at, timeZone) },
    {
      key: 'completed_at',
      header: 'Completed',
      render: (row) => (row.completed_at ? formatDateTime(row.completed_at, timeZone) : 'Not yet'),
    },
    {
      key: 'actions',
      header: 'View',
      render: (row) => (
        <Link className="btn-secondary h-8" to={`/f/${encodeURIComponent(factoryCode)}/runs/${row.id}`}>
          <Icon name="eye" />
          Open
        </Link>
      ),
    },
  ]
}

export function OrderRunsTab({ orderId }: { orderId: string }) {
  const factory = useFactory()
  const query = useQuery({
    queryKey: ['order-runs', orderId],
    queryFn: () =>
      unwrap(api.GET('/api/v1/orders/{order_id}/runs', { params: { path: { order_id: orderId }, query: { limit: 50, offset: 0 } } })),
  })

  if (query.isPending) return <LoadingState label="Loading runs…" variant="table" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data.items.length === 0) {
    return <EmptyState icon="info" title="No analysis runs" description="Start an analysis to see runs here." />
  }
  return (
    <DataTable
      caption="Analysis runs"
      columns={columns(factory.code, factory.timezone)}
      rows={query.data.items}
      rowKey={(row) => row.id}
    />
  )
}
