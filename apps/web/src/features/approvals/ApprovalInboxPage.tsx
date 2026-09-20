import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { SourceLabel } from '../../components/SourceLabel'
import { StateBadge } from '../../components/StateBadge'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDateTime, humanizeCode } from '../../lib/format'

type RecommendationSummary = Schemas['RecommendationSummary']
type InboxStatus = 'PROPOSED' | 'APPROVED'

const STATUS_TABS: { status: InboxStatus; label: string }[] = [
  { status: 'PROPOSED', label: 'Awaiting decision' },
  { status: 'APPROVED', label: 'Approved, ready to apply' },
]

function columns(factoryCode: string, timeZone: string): Column<RecommendationSummary>[] {
  return [
    {
      key: 'order',
      header: 'Order',
      render: (row) => <span className="font-mono text-xs font-medium">{row.order.external_ref}</span>,
    },
    { key: 'kind', header: 'Kind', render: (row) => humanizeCode(row.kind) },
    { key: 'created', header: 'Created', render: (row) => formatDateTime(row.created_at, timeZone) },
    { key: 'expires', header: 'Expires', render: (row) => formatDateTime(row.expires_at, timeZone) },
    {
      key: 'source',
      header: 'Source',
      render: (row) => (
        <SourceLabel source={row.generated_by === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }} />
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <div className="flex flex-col items-start gap-1">
          <StateBadge vocabulary="recommendation" state={row.status} />
          {row.expired && <StateBadge vocabulary="recommendation" state="EXPIRED" />}
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Review',
      render: (row) => (
        <Link className="btn-secondary h-8" to={`/f/${encodeURIComponent(factoryCode)}/approvals/${row.id}`}>
          <Icon name="eye" />
          Review
        </Link>
      ),
    },
  ]
}

function ApprovalInbox() {
  const factory = useFactory()
  const [status, setStatus] = useState<InboxStatus>('PROPOSED')

  const query = useQuery({
    queryKey: ['recommendations', factory.id, status],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/recommendations', {
          params: { path: { factory_id: factory.id }, query: { status, limit: 50, offset: 0 } },
        }),
      ),
  })

  let body
  if (query.isPending) {
    body = <LoadingState label="Loading recommendations…" variant="table" />
  } else if (query.isError) {
    body =
      query.error instanceof ApiError && query.error.status === 403 ? (
        <PermissionDenied />
      ) : (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      )
  } else if (query.data.items.length === 0) {
    body = (
      <EmptyState
        icon="info"
        title="Nothing here"
        description={
          status === 'PROPOSED'
            ? 'No recommendations are awaiting a decision.'
            : 'No approved recommendations are waiting to be applied.'
        }
      />
    )
  } else {
    body = (
      <DataTable
        caption="Recommendations"
        columns={columns(factory.code, factory.timezone)}
        rows={query.data.items}
        rowKey={(row) => row.id}
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div role="tablist" aria-label="Recommendation status" className="flex gap-2">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.status}
            type="button"
            role="tab"
            aria-selected={status === tab.status}
            className={status === tab.status ? 'btn-primary h-8' : 'btn-secondary h-8'}
            onClick={() => {
              setStatus(tab.status)
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div aria-busy={query.isFetching}>{body}</div>
    </div>
  )
}

export function ApprovalInboxPage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Recommendations awaiting a decision, and approved recommendations ready to apply."
      />
      {can('analysis:read') ? (
        <ApprovalInbox />
      ) : (
        <PermissionDenied message="Your role does not include access to approvals." />
      )}
    </>
  )
}
