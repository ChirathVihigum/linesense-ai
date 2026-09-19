import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { Pagination } from '../../components/Pagination'
import { StateBadge } from '../../components/StateBadge'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDateTime, formatDecimal } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'

type Reservation = Schemas['MaterialReservationOut']

const PAGE_SIZE = 20

function shortId(id: string): string {
  return id.slice(0, 8)
}

/**
 * Factory reservations. The list endpoint returns raw ids (no joined material
 * or order names), so ids are shown short, monospace, with the full id on
 * hover, rather than inventing labels the server did not send.
 */
export function ReservationTable() {
  const factory = useFactory()
  const can = useCan()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const [offset, setOffset] = useState(0)
  const [confirming, setConfirming] = useState<Reservation | null>(null)

  const query = useQuery({
    queryKey: ['reservations', factory.id, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/reservations', {
          params: { path: { factory_id: factory.id }, query: { limit: PAGE_SIZE, offset } },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const release = useMutation({
    mutationFn: ({ reservationId, key }: { reservationId: string; key: string }) =>
      unwrap(
        api.POST('/api/v1/reservations/{reservation_id}/release', {
          params: { path: { reservation_id: reservationId }, header: { 'Idempotency-Key': key } },
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      setConfirming(null)
      await queryClient.invalidateQueries({ queryKey: ['reservations', factory.id] })
      await queryClient.invalidateQueries({ queryKey: ['materials', factory.id] })
    },
    onError: () => {
      // Close the dialog so the ErrorState below (rendered in the page, not the
      // dialog) is visible instead of being covered by the modal overlay.
      setConfirming(null)
    },
  })

  const columns: Column<Reservation>[] = [
    { key: 'id', header: 'Reservation', render: (row) => <code className="font-mono text-xs" title={row.id}>{shortId(row.id)}</code> },
    { key: 'material', header: 'Material', render: (row) => <code className="font-mono text-xs" title={row.material_id}>{shortId(row.material_id)}</code> },
    { key: 'order', header: 'Order', render: (row) => <code className="font-mono text-xs" title={row.order_id}>{shortId(row.order_id)}</code> },
    { key: 'quantity', header: 'Quantity', align: 'right', render: (row) => formatDecimal(row.quantity) },
    { key: 'status', header: 'Status', render: (row) => <StateBadge vocabulary="reservation" state={row.status} /> },
    { key: 'created_at', header: 'Created', render: (row) => formatDateTime(row.created_at, factory.timezone) },
    {
      key: 'actions',
      header: 'Actions',
      render: (row) =>
        can('inventory:write') && row.status === 'ACTIVE' ? (
          <button
            type="button"
            className="btn-secondary h-8"
            onClick={() => {
              setConfirming(row)
            }}
          >
            Release
          </button>
        ) : null,
    },
  ]

  if (query.isPending) return <LoadingState label="Loading reservations…" variant="table" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? null : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }
  if (query.data.items.length === 0) {
    return <EmptyState icon="info" title="No reservations" description="Material reservations for orders will appear here." />
  }

  return (
    <>
      {release.isError && <ErrorState title="The reservation was not released" error={release.error} />}
      <DataTable caption="Reservations" columns={columns} rows={query.data.items} rowKey={(row) => row.id} />
      <Pagination total={query.data.total} limit={query.data.limit} offset={query.data.offset} onOffsetChange={setOffset} />
      {confirming && (
        <ConfirmDialog
          open
          title="Release this reservation?"
          confirmLabel={release.isPending ? 'Releasing…' : 'Release'}
          pending={release.isPending}
          onCancel={() => {
            setConfirming(null)
          }}
          onConfirm={() => {
            release.mutate({
              reservationId: confirming.id,
              key: idempotency.keyFor({ reservation_id: confirming.id }),
            })
          }}
        >
          <p>
            <Icon name="unlock" className="mr-1 inline h-4 w-4" />
            Releasing frees {formatDecimal(confirming.quantity)} units back to available stock.
          </p>
        </ConfirmDialog>
      )}
    </>
  )
}
