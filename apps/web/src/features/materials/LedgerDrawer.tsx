import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { Pagination } from '../../components/Pagination'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime, formatDecimal } from '../../lib/format'

type Movement = Schemas['MovementOut']

const PAGE_SIZE = 20

const MOVEMENT_LABEL: Record<string, string> = {
  RECEIPT: 'Receipt',
  ISSUE: 'Issue',
  CORRECTION: 'Correction',
}

function shortId(id: string): string {
  return id.slice(0, 8)
}

function columns(timeZone: string): Column<Movement>[] {
  return [
    {
      key: 'created_at',
      header: 'When',
      render: (row) => <span className="whitespace-nowrap">{formatDateTime(row.created_at, timeZone)}</span>,
    },
    { key: 'type', header: 'Type', render: (row) => MOVEMENT_LABEL[row.movement_type] ?? row.movement_type },
    { key: 'lot', header: 'Lot', render: (row) => <span className="font-mono text-xs">{row.lot_code ?? 'None'}</span> },
    {
      key: 'quantity',
      header: 'Quantity',
      align: 'right',
      render: (row) => {
        const value = Number(row.quantity)
        const sign = value > 0 ? '+' : ''
        return (
          <span className={value < 0 ? 'text-bad-fg' : 'text-ok-fg'}>
            {sign}
            {formatDecimal(row.quantity, 4)}
          </span>
        )
      },
    },
    { key: 'reason', header: 'Reason', render: (row) => row.reason ?? 'None' },
    {
      key: 'actor',
      header: 'Actor',
      render: (row) => <span className="font-mono text-xs">{row.created_by ? shortId(row.created_by) : 'System'}</span>,
    },
    {
      key: 'correction',
      header: 'Correction',
      render: (row) =>
        row.corrects_movement_id ? (
          <span className="text-xs text-fg-muted">
            Corrects{' '}
            <code className="font-mono" title={row.corrects_movement_id}>
              {shortId(row.corrects_movement_id)}
            </code>
          </span>
        ) : (
          <code className="font-mono text-xs text-fg-muted" title={row.id}>
            {shortId(row.id)}
          </code>
        ),
    },
  ]
}

/**
 * A material's movement ledger: paginated, newest first (matches the server
 * order). Rendered inline (not a floating overlay) so it never needs its own
 * focus trap; `onClose` collapses it back.
 */
export function LedgerDrawer({
  materialId,
  materialLabel,
  onClose,
}: {
  materialId: string
  materialLabel: string
  onClose: () => void
}) {
  const factory = useFactory()
  const [offset, setOffset] = useState(0)

  const query = useQuery({
    queryKey: ['material-ledger', factory.id, materialId, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/materials/{material_id}/ledger', {
          params: { path: { factory_id: factory.id, material_id: materialId }, query: { limit: PAGE_SIZE, offset } },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  return (
    <section aria-label={`Ledger for ${materialLabel}`} className="panel flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Ledger: {materialLabel}</h2>
        <button type="button" className="btn-secondary h-8" onClick={onClose}>
          <Icon name="x" />
          Close
        </button>
      </div>
      {query.isPending ? (
        <LoadingState label="Loading the ledger…" variant="table" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState icon="info" title="No movements yet" description="Receipts, issues and corrections will appear here." />
      ) : (
        <>
          <DataTable
            caption={`Movements for ${materialLabel}`}
            columns={columns(factory.timezone)}
            rows={query.data.items}
            rowKey={(row) => row.id}
          />
          <Pagination total={query.data.total} limit={query.data.limit} offset={query.data.offset} onOffsetChange={setOffset} />
        </>
      )}
    </section>
  )
}
