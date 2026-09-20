import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import type { Schemas } from '../../lib/api'
import { formatDecimal } from '../../lib/format'

type Allocation = Schemas['AllocationOut']

const COLUMNS: Column<Allocation>[] = [
  { key: 'slot_id', header: 'Slot', render: (row) => <span className="font-mono text-xs">{row.slot_id.slice(0, 8)}</span> },
  { key: 'units', header: 'Units', align: 'right', render: (row) => formatDecimal(row.units, 0) },
  {
    key: 'standard_minutes',
    header: 'Standard minutes',
    align: 'right',
    render: (row) => formatDecimal(row.standard_minutes),
  },
  { key: 'status', header: 'Status', render: (row) => row.status },
]

/** The order's capacity allocations (from `GET /orders/{id}`, not a full board join). */
export function OrderPlanTab({ order }: { order: Schemas['OrderDetail'] }) {
  if (order.allocations.length === 0) {
    return <EmptyState icon="calendar" title="No allocations" description="This order has not been scheduled on the capacity board yet." />
  }
  return <DataTable caption="Allocations" columns={COLUMNS} rows={order.allocations} rowKey={(row) => row.id} />
}
