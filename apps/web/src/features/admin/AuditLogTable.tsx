import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { LoadingState } from '../../components/LoadingState'
import { Pagination } from '../../components/Pagination'
import { StateBadge } from '../../components/StateBadge'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime } from '../../lib/format'
import { useDebouncedValue } from '../../lib/useDebouncedValue'

type AuditEvent = Schemas['AuditEventOut']

const PAGE_SIZE = 25

function columns(timeZone: string): Column<AuditEvent>[] {
  return [
    {
      key: 'created_at',
      header: 'When',
      render: (row) => <span className="whitespace-nowrap">{formatDateTime(row.created_at, timeZone)}</span>,
    },
    { key: 'action', header: 'Action', render: (row) => <span className="font-mono text-xs">{row.action}</span> },
    {
      key: 'target',
      header: 'Target',
      render: (row) => (
        <span className="font-mono text-xs">
          {row.target_type}:{row.target_id}
        </span>
      ),
    },
    { key: 'actor', header: 'Actor', render: (row) => <span className="font-mono text-xs">{row.actor_id}</span> },
    { key: 'outcome', header: 'Outcome', render: (row) => <StateBadge vocabulary="audit_outcome" state={row.outcome} /> },
    { key: 'reason', header: 'Reason', render: (row) => row.reason ?? 'None' },
    { key: 'trace', header: 'Trace ID', render: (row) => <span className="font-mono text-xs">{row.trace_id ?? 'None'}</span> },
  ]
}

export function AuditLogTable() {
  const factory = useFactory()
  const actionFilterId = useId()
  const targetFilterId = useId()
  const [action, setAction] = useState('')
  const [targetType, setTargetType] = useState('')
  const [offset, setOffset] = useState(0)
  const debouncedAction = useDebouncedValue(action, 300)
  const debouncedTargetType = useDebouncedValue(targetType, 300)

  const query = useQuery({
    queryKey: ['audit-events', factory.id, debouncedAction, debouncedTargetType, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/audit-events', {
          params: {
            path: { factory_id: factory.id },
            query: {
              action: debouncedAction || undefined,
              target_type: debouncedTargetType || undefined,
              limit: PAGE_SIZE,
              offset,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })

  return (
    <div className="flex flex-col gap-4">
      <form role="search" className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField id={actionFilterId} label="Action">
          <input
            id={actionFilterId}
            className="input"
            value={action}
            onChange={(event) => {
              setAction(event.target.value)
              setOffset(0)
            }}
          />
        </FormField>
        <FormField id={targetFilterId} label="Target type">
          <input
            id={targetFilterId}
            className="input"
            value={targetType}
            onChange={(event) => {
              setTargetType(event.target.value)
              setOffset(0)
            }}
          />
        </FormField>
      </form>
      {query.isPending && <LoadingState label="Loading audit events…" variant="table" />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.isSuccess && query.data.items.length === 0 && (
        <EmptyState icon="info" title="No audit events" description="Matching events will appear here." />
      )}
      {query.isSuccess && query.data.items.length > 0 && (
        <>
          <DataTable caption="Audit log" columns={columns(factory.timezone)} rows={query.data.items} rowKey={(row) => String(row.id)} />
          <Pagination total={query.data.total} limit={query.data.limit} offset={query.data.offset} onOffsetChange={setOffset} />
        </>
      )}
    </div>
  )
}
