import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { SourceLabel } from '../../components/SourceLabel'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDate, formatDecimal } from '../../lib/format'
import { LedgerDrawer } from './LedgerDrawer'
import { ReservationTable } from './ReservationTable'
import { StockMovementForms } from './StockMovementForms'

type MaterialStatus = Schemas['MaterialStatusOut']

function ReorderBadge({ belowReorderPoint }: { belowReorderPoint: boolean }) {
  if (belowReorderPoint) {
    return (
      <span className="inline-flex h-6 items-center gap-1 rounded-full border border-bad-line bg-bad-bg px-2 text-xs font-medium whitespace-nowrap text-bad-fg">
        <Icon name="alert" className="h-3.5 w-3.5" />
        Below reorder point
      </span>
    )
  }
  return (
    <span className="inline-flex h-6 items-center gap-1 rounded-full border border-ok-line bg-ok-bg px-2 text-xs font-medium whitespace-nowrap text-ok-fg">
      <Icon name="check" className="h-3.5 w-3.5" />
      Above reorder point
    </span>
  )
}

function columns(onOpenLedger: (material: MaterialStatus) => void): Column<MaterialStatus>[] {
  return [
    {
      key: 'material',
      header: 'Material',
      render: (row) => (
        <div>
          <div>{row.material_name}</div>
          <div className="font-mono text-xs text-fg-muted">
            {row.material_code} · {row.unit}
          </div>
        </div>
      ),
    },
    { key: 'on_hand', header: 'On hand', align: 'right', render: (row) => formatDecimal(row.on_hand) },
    { key: 'reserved', header: 'Reserved', align: 'right', render: (row) => formatDecimal(row.reserved) },
    {
      key: 'available_now',
      header: 'Available now',
      align: 'right',
      render: (row) => formatDecimal(row.available_now),
    },
    {
      key: 'open_receipts',
      header: 'Open receipts',
      align: 'right',
      render: (row) => (
        <div>
          <div>{formatDecimal(row.open_receipt_quantity)}</div>
          {row.next_receipt_date && (
            <div className="text-xs text-fg-muted">Due {formatDate(row.next_receipt_date)}</div>
          )}
        </div>
      ),
    },
    {
      key: 'average_daily_consumption',
      header: 'Avg. daily use',
      align: 'right',
      render: (row) => formatDecimal(row.average_daily_consumption),
    },
    {
      key: 'coverage_days',
      header: 'Coverage (days)',
      align: 'right',
      // A null coverage renders "Unknown" (never a made-up number): consumption is zero,
      // so days-of-cover is undefined rather than infinite.
      render: (row) => formatDecimal(row.coverage_days, 1),
    },
    {
      key: 'reorder_point',
      header: 'Reorder point',
      align: 'right',
      render: (row) => formatDecimal(row.reorder_point),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <div className="flex flex-col items-start gap-1">
          <ReorderBadge belowReorderPoint={row.below_reorder_point} />
          <SourceLabel source={{ kind: 'calculated' }} />
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Ledger',
      render: (row) => (
        <button
          type="button"
          className="btn-secondary h-8"
          onClick={() => {
            onOpenLedger(row)
          }}
        >
          <Icon name="eye" />
          View ledger
        </button>
      ),
    },
  ]
}

function MaterialsOverview() {
  const factory = useFactory()
  const can = useCan()
  const [selected, setSelected] = useState<MaterialStatus | null>(null)

  const query = useQuery({
    queryKey: ['materials', factory.id],
    queryFn: () =>
      unwrap(api.GET('/api/v1/factories/{factory_id}/materials', { params: { path: { factory_id: factory.id } } })),
  })

  let body
  if (query.isPending) {
    body = <LoadingState label="Loading materials…" variant="table" />
  } else if (query.isError) {
    body =
      query.error instanceof ApiError && query.error.status === 403 ? (
        <PermissionDenied />
      ) : (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      )
  } else if (query.data.items.length === 0) {
    body = <EmptyState icon="info" title="No materials" description="Materials will appear here once configured." />
  } else {
    body = (
      <>
        <p className="mb-3 text-fg-muted">As of {formatDate(query.data.as_of)}.</p>
        <DataTable
          caption="Material overview"
          columns={columns((material) => {
            setSelected(material)
          })}
          rows={query.data.items}
          rowKey={(row) => row.material_id}
        />
      </>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <section>{body}</section>
      {selected && (
        <LedgerDrawer
          materialId={selected.material_id}
          materialLabel={`${selected.material_name} (${selected.material_code})`}
          onClose={() => {
            setSelected(null)
          }}
        />
      )}
      {can('inventory:write') && query.data && (
        <section aria-labelledby="materials-write" className="panel p-6">
          <h2 id="materials-write" className="mb-4 text-base font-semibold">
            Record stock movement
          </h2>
          <StockMovementForms materials={query.data.items} />
        </section>
      )}
      <section aria-labelledby="materials-reservations">
        <h2 id="materials-reservations" className="mb-3 text-base font-semibold">
          Reservations
        </h2>
        <ReservationTable />
      </section>
    </div>
  )
}

export function MaterialsPage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="Materials"
        description="On-hand and reserved quantities, reorder status, and the movement ledger."
      />
      {can('inventory:read') ? (
        <MaterialsOverview />
      ) : (
        <PermissionDenied message="Your role does not include access to materials." />
      )}
    </>
  )
}
