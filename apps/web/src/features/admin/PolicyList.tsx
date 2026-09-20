import { useQuery } from '@tanstack/react-query'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { StateBadge } from '../../components/StateBadge'
import { api, unwrap, type Schemas } from '../../lib/api'
import { formatInteger } from '../../lib/format'

type PolicyVersion = Schemas['PolicyVersionOut']

function ruleValue(value: unknown): string {
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  return 'Unknown'
}

const COLUMNS: Column<PolicyVersion>[] = [
  { key: 'code', header: 'Code', render: (row) => <span className="font-mono text-xs">{row.code}</span> },
  { key: 'version', header: 'Version', align: 'right', render: (row) => formatInteger(row.version_no) },
  { key: 'status', header: 'Status', render: (row) => <StateBadge vocabulary="policy" state={row.status} /> },
  {
    key: 'demo',
    header: 'Demo',
    render: (row) =>
      row.is_demo ? (
        <span className="inline-flex h-6 items-center rounded-full border border-warn-line bg-warn-bg px-2 text-xs font-medium text-warn-fg">
          Demo policy
        </span>
      ) : (
        'No'
      ),
  },
  {
    key: 'rules',
    header: 'Sample size / max defects',
    render: (row) => `${ruleValue(row.rules.sample_size)} / ${ruleValue(row.rules.max_defective_units)}`,
  },
]

/** Every quality policy version in the organization (`admin:manage`). */
export function PolicyList() {
  const query = useQuery({
    queryKey: ['admin-policies'],
    queryFn: () => unwrap(api.GET('/api/v1/admin/policies', { params: { query: { limit: 100, offset: 0 } } })),
  })

  if (query.isPending) return <LoadingState label="Loading quality policies…" variant="table" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data.items.length === 0) {
    return <EmptyState icon="info" title="No quality policies" description="Quality policy versions will appear here once configured." />
  }
  return <DataTable caption="Quality policy versions" columns={COLUMNS} rows={query.data.items} rowKey={(row) => row.id} />
}
