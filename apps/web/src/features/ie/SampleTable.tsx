import { DataTable, type Column } from '../../components/DataTable'
import { Icon } from '../../components/Icon'
import type { Schemas } from '../../lib/api'
import { formatDecimal, formatInteger } from '../../lib/format'

type OperationAnalysis = Schemas['OperationAnalysisOut']
type LineBalance = Schemas['LineBalanceOut']

function InsufficientBadge({ insufficient }: { insufficient: boolean }) {
  if (!insufficient) return <span className="text-fg-muted">Enough samples</span>
  return (
    <span className="inline-flex items-center gap-1 font-medium text-warn-fg">
      <Icon name="alert" className="h-3.5 w-3.5" />
      Insufficient samples
    </span>
  )
}

/**
 * The data-table alternative to `BottleneckChart`: one row per operation with
 * its SAM, sample count, representative and effective cycle time, and an
 * explicit "insufficient samples" marker (icon + text) rather than a chart
 * bar that could be missed.
 */
export function SampleTable({
  operations,
  balance,
}: {
  operations: OperationAnalysis[]
  balance: LineBalance | null
}) {
  const columns: Column<OperationAnalysis & { index: number }>[] = [
    {
      key: 'operation',
      header: 'Operation',
      render: (row) => (
        <span className={row.index === balance?.bottleneck_index ? 'font-semibold' : undefined}>
          {row.code} {row.name}
          {row.index === balance?.bottleneck_index && ' (bottleneck)'}
        </span>
      ),
    },
    { key: 'sam', header: 'SAM (min)', align: 'right', render: (row) => formatDecimal(row.sam_minutes, 4) },
    { key: 'samples', header: 'Samples', align: 'right', render: (row) => formatInteger(row.sample_count) },
    {
      key: 'representative',
      header: 'Representative (s)',
      align: 'right',
      render: (row) => formatDecimal(row.representative_seconds, 2),
    },
    {
      key: 'effective',
      header: 'Effective (s)',
      align: 'right',
      render: (row) => formatDecimal(row.effective_seconds, 2),
    },
    { key: 'parallel', header: 'Parallel operators', align: 'right', render: (row) => formatInteger(row.parallel_operators) },
    { key: 'flag', header: 'Sample size', render: (row) => <InsufficientBadge insufficient={row.insufficient_samples} /> },
  ]

  return (
    <DataTable
      caption="Operations: SAM, sample counts and effective cycle time"
      columns={columns}
      rows={operations.map((operation, index) => ({ ...operation, index }))}
      rowKey={(row) => row.operation_id}
    />
  )
}
