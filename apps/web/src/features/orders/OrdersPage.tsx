import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { Pagination } from '../../components/Pagination'
import { PermissionDenied } from '../../components/PermissionDenied'
import { StateBadge } from '../../components/StateBadge'
import { STATE_STYLES, type StateVocabulary } from '../../components/stateStyles'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDate, formatInteger } from '../../lib/format'
import { useDebouncedValue } from '../../lib/useDebouncedValue'

type OrderSummary = Schemas['OrderSummary']

export const ORDERS_PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 300

const STATE_FILTERS: { param: string; label: string; vocabulary: StateVocabulary }[] = [
  { param: 'production_state', label: 'Production', vocabulary: 'production' },
  { param: 'material_state', label: 'Material', vocabulary: 'material' },
  { param: 'quality_state', label: 'Quality', vocabulary: 'quality' },
]
const FILTER_PARAMS = ['q', 'production_state', 'material_state', 'quality_state', 'due_before']

type ProductionState = 'DRAFT' | 'VALIDATED' | 'PLANNED' | 'IN_PRODUCTION' | 'PRODUCTION_COMPLETE' | 'DISPATCHED' | 'CANCELLED'
type MaterialState = 'UNKNOWN' | 'READY' | 'AT_RISK' | 'SHORTAGE'
type QualityState = 'NOT_INSPECTED' | 'PENDING' | 'HOLD' | 'RELEASED'

function ShipmentCell({ shipment }: { shipment: OrderSummary['shipment'] }) {
  if (shipment.eligible) {
    return (
      <span className="inline-flex h-6 items-center gap-1 rounded-full border border-ok-line bg-ok-bg px-2 text-xs font-medium whitespace-nowrap text-ok-fg">
        <Icon name="truck" className="h-3.5 w-3.5" />
        <span>Eligible</span>
      </span>
    )
  }
  return (
    <div className="flex flex-col gap-1">
      <span className="inline-flex h-6 w-fit items-center gap-1 rounded-full border border-neutral-line bg-neutral-bg px-2 text-xs font-medium whitespace-nowrap text-neutral-fg">
        <Icon name="ban" className="h-3.5 w-3.5" />
        <span>Not eligible</span>
      </span>
      {shipment.reasons.length > 0 && (
        <ul className="max-w-56 text-xs text-fg-muted">
          {shipment.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

const COLUMNS: Column<OrderSummary>[] = [
  {
    key: 'external_ref',
    header: 'Order',
    render: (order) => <span className="font-mono text-xs font-medium">{order.external_ref}</span>,
  },
  {
    key: 'customer',
    header: 'Customer',
    render: (order) => (
      <div>
        <div>{order.customer.name}</div>
        <div className="font-mono text-xs text-fg-muted">{order.customer.code}</div>
      </div>
    ),
  },
  {
    key: 'style',
    header: 'Style',
    render: (order) => (
      <div>
        <div>{order.style.name}</div>
        <div className="font-mono text-xs text-fg-muted">{order.style.code}</div>
      </div>
    ),
  },
  { key: 'quantity', header: 'Quantity', align: 'right', render: (order) => formatInteger(order.quantity) },
  {
    key: 'progress',
    header: 'Produced / packed',
    align: 'right',
    render: (order) => `${formatInteger(order.produced_units)} / ${formatInteger(order.packed_units)}`,
  },
  { key: 'due_date', header: 'Due date', render: (order) => <span className="whitespace-nowrap">{formatDate(order.due_date)}</span> },
  { key: 'priority', header: 'Priority', align: 'right', render: (order) => formatInteger(order.priority) },
  {
    key: 'production_state',
    header: 'Production',
    render: (order) => <StateBadge vocabulary="production" state={order.production_state} />,
  },
  {
    key: 'material_state',
    header: 'Materials',
    render: (order) => <StateBadge vocabulary="material" state={order.material_state} />,
  },
  {
    key: 'quality_state',
    header: 'Quality',
    render: (order) => <StateBadge vocabulary="quality" state={order.quality_state} />,
  },
  { key: 'shipment', header: 'Shipment', render: (order) => <ShipmentCell shipment={order.shipment} /> },
]

export function OrdersPage() {
  const factory = useFactory()
  const can = useCan()
  const [searchParams, setSearchParams] = useSearchParams()

  const q = searchParams.get('q') ?? ''
  const productionState = searchParams.get('production_state') ?? ''
  const materialState = searchParams.get('material_state') ?? ''
  const qualityState = searchParams.get('quality_state') ?? ''
  const dueBefore = searchParams.get('due_before') ?? ''
  const offset = Math.max(0, Number(searchParams.get('offset') ?? 0) || 0)

  const [searchText, setSearchText] = useState(q)
  const debouncedSearch = useDebouncedValue(searchText.trim(), SEARCH_DEBOUNCE_MS)

  /** Sets (or clears) URL params; any filter change returns to the first page. */
  const updateParams = (changes: Record<string, string>) => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value)
          else next.delete(key)
        }
        if (!('offset' in changes)) next.delete('offset')
        return next
      },
      { replace: true },
    )
  }

  useEffect(() => {
    if (debouncedSearch === q) return
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (debouncedSearch) next.set('q', debouncedSearch)
        else next.delete('q')
        next.delete('offset')
        return next
      },
      { replace: true },
    )
  }, [debouncedSearch, q, setSearchParams])

  const query = useQuery({
    queryKey: ['orders', factory.id, { q, productionState, materialState, qualityState, dueBefore, offset }],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/orders', {
          params: {
            path: { factory_id: factory.id },
            query: {
              q: q || undefined,
              production_state: (productionState || undefined) as ProductionState | undefined,
              material_state: (materialState || undefined) as MaterialState | undefined,
              quality_state: (qualityState || undefined) as QualityState | undefined,
              due_before: dueBefore || undefined,
              limit: ORDERS_PAGE_SIZE,
              offset,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  const filtersActive = FILTER_PARAMS.some((param) => searchParams.has(param))
  const factoryPath = `/f/${encodeURIComponent(factory.code)}`

  const actions = (
    <>
      {can('order:import') && (
        <Link className="btn-secondary" to={`${factoryPath}/orders/import`}>
          <Icon name="upload" />
          Import orders
        </Link>
      )}
      {can('order:create') && (
        <Link className="btn-primary" to={`${factoryPath}/orders/new`}>
          <Icon name="plus" />
          New order
        </Link>
      )}
    </>
  )

  let body
  if (query.isPending) {
    body = <LoadingState label="Loading orders…" variant="table" />
  } else if (query.isError) {
    body =
      query.error instanceof ApiError && query.error.status === 403 ? (
        <PermissionDenied />
      ) : (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      )
  } else if (query.data.items.length === 0) {
    body = filtersActive ? (
      <EmptyState
        icon="search"
        title="No orders match these filters"
        description="Change the search or filters to see more orders."
        action={
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setSearchText('')
              setSearchParams(new URLSearchParams(), { replace: true })
            }}
          >
            Clear filters
          </button>
        }
      />
    ) : (
      <EmptyState
        icon="file-csv"
        title="No orders yet"
        description={
          can('order:create')
            ? 'Create an order or import a CSV file to start planning.'
            : 'Orders created by planners will appear here.'
        }
      />
    )
  } else {
    body = (
      <>
        <DataTable
          caption="Orders"
          columns={COLUMNS}
          rows={query.data.items}
          rowKey={(order) => order.id}
          sort={{ key: 'due_date', direction: 'ascending' }}
        />
        <Pagination
          total={query.data.total}
          limit={query.data.limit}
          offset={query.data.offset}
          onOffsetChange={(nextOffset) => {
            updateParams({ offset: nextOffset > 0 ? String(nextOffset) : '' })
          }}
        />
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Orders"
        description={`Customer orders for ${factory.name}, earliest due date first.`}
        actions={actions}
      />
      <form
        role="search"
        aria-label="Filter orders"
        className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-[minmax(14rem,2fr)_repeat(4,minmax(8rem,1fr))]"
        onSubmit={(event) => {
          event.preventDefault()
        }}
      >
        <div className="col-span-2 flex flex-col gap-1.5 lg:col-span-1">
          <label htmlFor="orders-search" className="text-xs font-medium text-fg-muted">
            Search orders
          </label>
          <span className="relative">
            <Icon name="search" className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-fg-muted" />
            <input
              id="orders-search"
              type="search"
              className="input pl-8"
              placeholder="Order ref, customer or style"
              value={searchText}
              maxLength={100}
              onChange={(event) => {
                setSearchText(event.target.value)
              }}
            />
          </span>
        </div>
        {STATE_FILTERS.map((filter) => (
          <div key={filter.param} className="flex flex-col gap-1.5">
            <label htmlFor={`orders-filter-${filter.param}`} className="text-xs font-medium text-fg-muted">
              {filter.label}
            </label>
            <select
              id={`orders-filter-${filter.param}`}
              className="input"
              value={searchParams.get(filter.param) ?? ''}
              onChange={(event) => {
                updateParams({ [filter.param]: event.target.value })
              }}
            >
              <option value="">All</option>
              {Object.entries(STATE_STYLES[filter.vocabulary]).map(([state, style]) => (
                <option key={state} value={state}>
                  {style.label}
                </option>
              ))}
            </select>
          </div>
        ))}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="orders-due-before" className="text-xs font-medium text-fg-muted">
            Due before
          </label>
          <input
            id="orders-due-before"
            type="date"
            className="input"
            value={dueBefore}
            onChange={(event) => {
              updateParams({ due_before: event.target.value })
            }}
          />
        </div>
      </form>
      <div aria-busy={query.isFetching}>{body}</div>
    </>
  )
}
