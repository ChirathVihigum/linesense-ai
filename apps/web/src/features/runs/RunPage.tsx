import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { ConfirmDialog } from '../../components/ConfirmDialog'
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
import { formatDateTime, formatInteger } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'
import { AgentResultCard } from './AgentResultCard'
import { RunTimeline } from './RunTimeline'

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING'])
const CANCELLABLE_STATUSES = new Set(['QUEUED', 'RUNNING', 'AWAITING_REVIEW'])
const RETRYABLE_STATUSES = new Set(['FAILED', 'DEGRADED', 'CANCELLED'])
const POLL_INTERVAL_MS = 2000

function LlmBadge({ llm }: { llm: Schemas['LlmLabel'] }) {
  if (llm.is_fixture) return <SourceLabel source={{ kind: 'test_fixture' }} />
  return (
    <span className="inline-flex h-6 items-center gap-1 rounded-md border border-info-line bg-info-bg px-2 text-xs font-medium text-info-fg">
      <Icon name="sparkle" className="h-3.5 w-3.5" />
      {llm.label}
    </span>
  )
}

function RunView({ runId }: { runId: string }) {
  const factory = useFactory()
  const can = useCan()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const [confirmCancel, setConfirmCancel] = useState(false)

  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}', { params: { path: { run_id: runId } } })),
    refetchInterval: (query) => (ACTIVE_STATUSES.has(query.state.data?.status ?? '') ? POLL_INTERVAL_MS : false),
  })

  const eventsQuery = useQuery({
    queryKey: ['run-events', runId],
    queryFn: () =>
      unwrap(api.GET('/api/v1/runs/{run_id}/events', { params: { path: { run_id: runId } } })),
    refetchInterval: () => (ACTIVE_STATUSES.has(runQuery.data?.status ?? '') ? POLL_INTERVAL_MS : false),
  })

  const cancelMutation = useMutation({
    mutationFn: (key: string) =>
      unwrap(
        api.POST('/api/v1/runs/{run_id}/cancel', {
          params: { path: { run_id: runId }, header: { 'Idempotency-Key': key } },
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      setConfirmCancel(false)
      showToast('Analysis run cancelled.')
      await queryClient.invalidateQueries({ queryKey: ['run', runId] })
    },
  })

  const retryMutation = useMutation({
    mutationFn: (key: string) =>
      unwrap(
        api.POST('/api/v1/runs/{run_id}/retry', {
          params: { path: { run_id: runId }, header: { 'Idempotency-Key': key } },
        }),
      ),
    onSuccess: async (accepted) => {
      idempotency.reset()
      showToast('A new analysis run was started.')
      await navigate(`/f/${encodeURIComponent(factory.code)}/runs/${accepted.run_id}`)
    },
  })

  if (runQuery.isPending) return <LoadingState label="Loading run…" variant="page" />
  if (runQuery.isError) {
    return runQuery.error instanceof ApiError && runQuery.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={runQuery.error} onRetry={() => void runQuery.refetch()} />
    )
  }
  const run = runQuery.data
  const canManage = can('analysis:run')

  return (
    <>
      <PageHeader
        title={`Analysis run for ${run.order.external_ref}`}
        description={
          <Link className="link" to={`/f/${encodeURIComponent(factory.code)}/orders/${run.order.id}`}>
            Back to order {run.order.external_ref}
          </Link>
        }
        actions={
          canManage ? (
            <>
              {CANCELLABLE_STATUSES.has(run.status) && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setConfirmCancel(true)
                  }}
                >
                  <Icon name="ban" />
                  Cancel run
                </button>
              )}
              {RETRYABLE_STATUSES.has(run.status) && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={retryMutation.isPending}
                  onClick={() => {
                    retryMutation.mutate(idempotency.keyFor({ runId }))
                  }}
                >
                  <Icon name="refresh" />
                  {retryMutation.isPending ? 'Starting…' : 'Retry'}
                </button>
              )}
            </>
          ) : undefined
        }
      />

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <StateBadge vocabulary="analysis" state={run.status} />
        <LlmBadge llm={run.llm} />
        <span className="text-sm text-fg-muted">
          Budget {formatInteger(run.model_calls_used)}/{formatInteger(run.model_calls_limit)} model calls ·{' '}
          {formatInteger(run.tokens_used)} tokens
        </span>
        <span className="text-sm text-fg-muted">Deadline {formatDateTime(run.deadline_at, factory.timezone)}</span>
      </div>

      {run.degraded_reason && (
        <div className="mb-6 rounded-md border border-warn-line bg-warn-bg p-3 text-warn-fg">
          Degraded: {run.degraded_reason}
        </div>
      )}
      {cancelMutation.isError && (
        <div className="mb-6">
          <ErrorState title="The run was not cancelled" error={cancelMutation.error} />
        </div>
      )}
      {retryMutation.isError && (
        <div className="mb-6">
          <ErrorState title="A new run was not started" error={retryMutation.error} />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-base font-semibold">Timeline</h2>
          {eventsQuery.isPending ? (
            <LoadingState label="Loading events…" />
          ) : eventsQuery.isError ? (
            <ErrorState error={eventsQuery.error} onRetry={() => void eventsQuery.refetch()} />
          ) : (
            <RunTimeline events={eventsQuery.data} timeZone={factory.timezone} />
          )}
        </section>
        <section>
          <h2 className="mb-3 text-base font-semibold">Agent results</h2>
          {run.results.length === 0 ? (
            <EmptyState icon="info" title="No agent results yet" description="Results appear as agents reply." />
          ) : (
            <div className="flex flex-col gap-4">
              {run.results.map((result) => (
                <AgentResultCard
                  key={result.task_id}
                  result={result}
                  factoryId={factory.id}
                  timeZone={factory.timezone}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this analysis run?"
        confirmLabel="Cancel run"
        pending={cancelMutation.isPending}
        onConfirm={() => {
          cancelMutation.mutate(idempotency.keyFor({ runId, action: 'cancel' }))
        }}
        onCancel={() => {
          setConfirmCancel(false)
        }}
      >
        Any pending agent tasks are stopped and open recommendations from this run are superseded.
      </ConfirmDialog>
    </>
  )
}

export function RunPage() {
  const { runId } = useParams<{ runId: string }>()
  if (!runId) return null
  return <RunView runId={runId} />
}
