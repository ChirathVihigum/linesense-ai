import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { ApiError, api, unwrap } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { useDebouncedValue } from '../../lib/useDebouncedValue'
import { DefectTrendChart } from './DefectTrendChart'
import { HoldsTable } from './HoldsTable'
import { InspectionForm } from './InspectionForm'
import { ReleaseReview } from './ReleaseReview'

const SEARCH_DEBOUNCE_MS = 300

function shortId(id: string): string {
  return id.slice(0, 8)
}

function OrderSearch({ onSelectOrder }: { onSelectOrder: (orderId: string) => void }) {
  const factory = useFactory()
  const [searchText, setSearchText] = useState('')
  const debounced = useDebouncedValue(searchText.trim(), SEARCH_DEBOUNCE_MS)

  const query = useQuery({
    queryKey: ['order-search', factory.id, debounced],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/orders', {
          params: { path: { factory_id: factory.id }, query: { q: debounced, limit: 5 } },
        }),
      ),
    enabled: debounced.length > 0,
  })

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor="quality-order-search" className="text-xs font-medium text-fg-muted">
        Find an order by reference
      </label>
      <span className="relative max-w-sm">
        <Icon name="search" className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-fg-muted" />
        <input
          id="quality-order-search"
          type="search"
          className="input pl-8"
          placeholder="Order reference"
          value={searchText}
          onChange={(event) => {
            setSearchText(event.target.value)
          }}
        />
      </span>
      {query.data && query.data.items.length > 0 && (
        <ul className="panel max-w-sm divide-y divide-line">
          {query.data.items.map((order) => (
            <li key={order.id}>
              <button
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-surface-sunken"
                onClick={() => {
                  onSelectOrder(order.id)
                  setSearchText('')
                }}
              >
                <span className="font-mono text-xs">{order.external_ref}</span>
                <span className="text-xs text-fg-muted">{order.customer.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {query.data && debounced.length > 0 && query.data.items.length === 0 && (
        <p className="text-xs text-fg-muted">No orders match &quot;{debounced}&quot;.</p>
      )}
    </div>
  )
}

function OrderQualityPanel({ orderId }: { orderId: string }) {
  const can = useCan()
  const query = useQuery({
    queryKey: ['order-quality', orderId],
    queryFn: () => unwrap(api.GET('/api/v1/orders/{order_id}/quality', { params: { path: { order_id: orderId } } })),
  })
  // A single-item lookup only for the heading: `OrderQualityOut` carries no external_ref, and a
  // hold row only has the order's id, so this is the cheapest available way to show the real
  // order reference instead of a bare id that could be mistaken for a PO reference.
  const orderSummary = useQuery({
    queryKey: ['order-summary', orderId],
    queryFn: () => unwrap(api.GET('/api/v1/orders/{order_id}', { params: { path: { order_id: orderId } } })),
  })

  if (query.isPending) return <LoadingState label="Loading order quality…" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-base font-semibold">
        {orderSummary.data ? (
          <>
            Order <span className="font-mono">{orderSummary.data.external_ref}</span>
          </>
        ) : (
          <>
            Order id <span className="font-mono">{shortId(orderId)}</span>
          </>
        )}
      </h2>
      {can('quality:inspect') && (
        <section aria-labelledby="quality-inspect" className="panel p-6">
          <h3 id="quality-inspect" className="mb-4 text-sm font-semibold">
            Record inspection
          </h3>
          <InspectionForm orderId={orderId} onRecorded={() => void query.refetch()} />
        </section>
      )}
      {can('quality:release') && (
        <section aria-labelledby="quality-release" className="panel p-6">
          <h3 id="quality-release" className="mb-4 text-sm font-semibold">
            Release for shipment
          </h3>
          <ReleaseReview orderQuality={query.data} />
        </section>
      )}
    </div>
  )
}

function QualityWorkspace() {
  const can = useCan()
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null)

  return (
    <div className="flex flex-col gap-8">
      <section aria-labelledby="quality-holds">
        <h2 id="quality-holds" className="mb-3 text-base font-semibold">
          Active holds
        </h2>
        <HoldsTable onSelectOrder={setSelectedOrderId} />
      </section>

      <section aria-labelledby="quality-inspection-section">
        <h2 id="quality-inspection-section" className="mb-3 text-base font-semibold">
          Inspect and release
        </h2>
        <OrderSearch onSelectOrder={setSelectedOrderId} />
        <div className="mt-4">
          {selectedOrderId ? (
            <OrderQualityPanel orderId={selectedOrderId} />
          ) : (
            <EmptyState icon="search" title="No order selected" description="Search for an order or pick one from active holds." />
          )}
        </div>
      </section>

      {can('quality:read') && (
        <section aria-labelledby="quality-trends">
          <h2 id="quality-trends" className="mb-3 text-base font-semibold">
            Defect trends
          </h2>
          <DefectTrendChart />
        </section>
      )}
    </div>
  )
}

export function QualityPage() {
  const can = useCan()
  return (
    <>
      <PageHeader title="Quality" description="Inspections, holds, releases and defect trends." />
      {can('quality:read') ? <QualityWorkspace /> : <PermissionDenied message="Your role does not include access to quality data." />}
    </>
  )
}
