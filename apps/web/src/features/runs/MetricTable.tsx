import { DataTable, type Column } from '../../components/DataTable'
import type { Schemas } from '../../lib/api'
import { formatDecimal, MISSING_VALUE } from '../../lib/format'

type Metric = Schemas['Metric']

const COLUMNS: Column<Metric>[] = [
  { key: 'name', header: 'Metric', render: (row) => row.name },
  { key: 'value', header: 'Value', align: 'right', render: (row) => formatDecimal(row.value) },
  { key: 'unit', header: 'Unit', render: (row) => row.unit || MISSING_VALUE },
  { key: 'note', header: 'Note', render: (row) => row.note ?? '' },
]

/** An agent result's metrics; a missing value renders "Unknown", never a made-up number. */
export function MetricTable({ metrics }: { metrics: Metric[] }) {
  if (metrics.length === 0) {
    return <p className="text-fg-muted">No metrics.</p>
  }
  return <DataTable caption="Metrics" columns={COLUMNS} rows={metrics} rowKey={(row) => row.name} />
}
