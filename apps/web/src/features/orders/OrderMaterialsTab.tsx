import { Link } from 'react-router'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { StateBadge } from '../../components/StateBadge'
import type { Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDecimal, formatInteger, formatPercent } from '../../lib/format'

type BomLine = Schemas['BomLineOut']
type Reservation = Schemas['ReservationOut']

function demandColumns(quantity: number): Column<BomLine>[] {
  return [
    {
      key: 'material',
      header: 'Material',
      render: (row) => (
        <div>
          <div>{row.material_name}</div>
          <div className="font-mono text-xs text-fg-muted">{row.material_code}</div>
        </div>
      ),
    },
    {
      key: 'quantity_per_unit',
      header: 'Per unit',
      align: 'right',
      render: (row) => `${formatDecimal(row.quantity_per_unit)} ${row.unit}`,
    },
    {
      key: 'wastage',
      header: 'Wastage',
      align: 'right',
      render: (row) => formatPercent(Number(row.wastage_fraction), 1),
    },
    {
      key: 'total_demand',
      header: `Total demand (${formatInteger(quantity)} units)`,
      align: 'right',
      render: (row) => {
        const perUnit = Number(row.quantity_per_unit) * (1 + Number(row.wastage_fraction))
        return `${formatDecimal((perUnit * quantity).toString())} ${row.unit}`
      },
    },
  ]
}

const RESERVATION_COLUMNS: Column<Reservation>[] = [
  {
    key: 'material_id',
    header: 'Material',
    render: (row) => <span className="font-mono text-xs">{row.material_id.slice(0, 8)}</span>,
  },
  { key: 'quantity', header: 'Reserved quantity', align: 'right', render: (row) => formatDecimal(row.quantity) },
  { key: 'status', header: 'Status', render: (row) => <StateBadge vocabulary="reservation" state={row.status} /> },
]

/** BOM demand and this order's material reservations. On-hand and available
 * quantities live on the Materials page (per-material, factory-wide), linked below,
 * since `GET /orders/{id}` does not resolve the reservations' materials to codes. */
export function OrderMaterialsTab({ order }: { order: Schemas['OrderDetail'] }) {
  const factory = useFactory()
  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="mb-2 text-base font-semibold">Bill of materials demand</h2>
        {order.bom.lines.length === 0 ? (
          <EmptyState icon="info" title="No BOM lines" description="This style's bill of materials has no lines." />
        ) : (
          <DataTable
            caption="Bill of materials demand"
            columns={demandColumns(order.quantity)}
            rows={order.bom.lines}
            rowKey={(row) => row.material_code}
          />
        )}
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold">Reservations</h2>
        {order.reservations.length === 0 ? (
          <EmptyState icon="info" title="No reservations" description="No material has been reserved for this order." />
        ) : (
          <DataTable
            caption="Reservations"
            columns={RESERVATION_COLUMNS}
            rows={order.reservations}
            rowKey={(row) => row.id}
          />
        )}
        <Link className="link mt-2 inline-block" to={`/f/${encodeURIComponent(factory.code)}/materials`}>
          View on-hand and available quantities on the Materials page
        </Link>
      </section>
    </div>
  )
}
