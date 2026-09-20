import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ShipmentEligibilityBadge } from '../../components/ShipmentEligibilityBadge'
import { StateBadge } from '../../components/StateBadge'
import type { Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime, formatInteger } from '../../lib/format'

type Inspection = Schemas['InspectionOut']
type Hold = Schemas['HoldOut']

function inspectionColumns(timeZone: string): Column<Inspection>[] {
  return [
    { key: 'type', header: 'Type', render: (row) => row.inspection_type },
    { key: 'inspected', header: 'Inspected', align: 'right', render: (row) => formatInteger(row.inspected_units) },
    { key: 'defective', header: 'Defective', align: 'right', render: (row) => formatInteger(row.defective_units) },
    { key: 'result', header: 'Result', render: (row) => row.result },
    { key: 'inspected_at', header: 'Inspected at', render: (row) => formatDateTime(row.inspected_at, timeZone) },
  ]
}

function holdColumns(timeZone: string): Column<Hold>[] {
  return [
    { key: 'reason', header: 'Reason', render: (row) => row.reason },
    { key: 'status', header: 'Status', render: (row) => row.status },
    { key: 'created_at', header: 'Created', render: (row) => formatDateTime(row.created_at, timeZone) },
    {
      key: 'released_at',
      header: 'Released',
      render: (row) => (row.released_at ? formatDateTime(row.released_at, timeZone) : 'Not released'),
    },
  ]
}

export function OrderQualityTab({ order }: { order: Schemas['OrderDetail'] }) {
  const factory = useFactory()
  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="mb-2 text-base font-semibold">Eligibility</h2>
        <div className="flex flex-wrap items-center gap-3">
          <StateBadge vocabulary="quality" state={order.quality_state} />
          <ShipmentEligibilityBadge eligible={order.shipment.eligible} eligibleLabel="Shipment eligible" />
        </div>
        {order.shipment.reasons.length > 0 && (
          <ul className="mt-1.5 text-sm text-fg-muted">
            {order.shipment.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold">Inspections</h2>
        {order.inspections.length === 0 ? (
          <EmptyState icon="info" title="No inspections" description="No quality inspection has been recorded yet." />
        ) : (
          <DataTable
            caption="Inspections"
            columns={inspectionColumns(factory.timezone)}
            rows={order.inspections}
            rowKey={(row) => row.id}
          />
        )}
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold">Holds and releases</h2>
        {order.holds.length === 0 ? (
          <EmptyState icon="info" title="No holds" description="This order has never been placed on hold." />
        ) : (
          <DataTable caption="Holds" columns={holdColumns(factory.timezone)} rows={order.holds} rowKey={(row) => row.id} />
        )}
      </section>
    </div>
  )
}
