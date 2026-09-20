import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { SourceLabel } from '../../components/SourceLabel'
import { StaleBanner } from '../../components/StaleBanner'
import { StateBadge } from '../../components/StateBadge'
import { ApiError, api, unwrap } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatDateTime, humanizeCode } from '../../lib/format'
import { useIdempotencyKey } from '../../lib/idempotency'
import { useToast } from '../../lib/toast'
import { EvidenceList } from '../evidence/EvidenceList'
import { fromEvidenceOut } from '../evidence/evidenceItem'
import { BLOCKED_REASON_TEXT } from './blockedReasons'
import { DecisionForm } from './DecisionForm'
import { DiffTables } from './DiffTables'

function StaleInputsList({ items }: { items: { field: string; message: string }[] }) {
  return (
    <ul className="mt-2 flex flex-col gap-1 text-sm">
      {items.map((item) => (
        <li key={`${item.field}-${item.message}`}>
          <span className="font-medium">{humanizeCode(item.field)}:</span> {item.message}
        </li>
      ))}
    </ul>
  )
}

function RecommendationView({ recId }: { recId: string }) {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const idempotency = useIdempotencyKey()
  const [confirmApply, setConfirmApply] = useState(false)

  const query = useQuery({
    queryKey: ['recommendation', recId],
    queryFn: () => unwrap(api.GET('/api/v1/recommendations/{rec_id}', { params: { path: { rec_id: recId } } })),
  })

  const applyMutation = useMutation({
    mutationFn: (key: string) =>
      unwrap(
        api.POST('/api/v1/recommendations/{rec_id}/apply', {
          params: { path: { rec_id: recId }, header: { 'Idempotency-Key': key } },
          body: { proposal_hash: query.data?.proposal_hash ?? '' },
        }),
      ),
    onSuccess: async () => {
      idempotency.reset()
      setConfirmApply(false)
      showToast('Recommendation applied.')
      await queryClient.invalidateQueries({ queryKey: ['recommendation', recId] })
    },
    onError: (error) => {
      // A 409 STALE_INPUT means the server already committed a status change
      // (superseded) before rejecting the apply; refetch so the page (and the
      // Apply button's can_apply/apply_blocked_reason) reflects it.
      if (error instanceof ApiError && error.code === 'STALE_INPUT') {
        void queryClient.invalidateQueries({ queryKey: ['recommendation', recId] })
      }
    },
  })

  if (query.isPending) return <LoadingState label="Loading recommendation…" variant="page" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }
  const rec = query.data
  const orderPath = `/f/${encodeURIComponent(factory.code)}/orders/${rec.order.id}`
  const staleFieldErrors =
    applyMutation.error instanceof ApiError && applyMutation.error.code === 'STALE_INPUT'
      ? applyMutation.error.fieldErrors
      : null

  return (
    <>
      <PageHeader
        title={`Recommendation for ${rec.order.external_ref}`}
        description={
          <Link className="link" to={orderPath}>
            Back to order {rec.order.external_ref}
          </Link>
        }
      />

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <StateBadge vocabulary="recommendation" state={rec.status} />
        <SourceLabel source={rec.generated_by === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }} />
        {rec.decision && (
          <SourceLabel
            source={{
              kind: 'approved',
              approvedBy: rec.decision.decided_by.display_name,
              approvedAt: rec.decision.decided_at,
              timeZone: factory.timezone,
            }}
          />
        )}
      </div>

      {rec.expired && (
        <div className="mb-6 flex flex-col gap-2">
          <StaleBanner message="This proposal has expired." />
          <Link className="link" to={orderPath}>
            Run new analysis
          </Link>
        </div>
      )}
      {!rec.expired && rec.stale && (
        <div className="mb-6 flex flex-col gap-2">
          <StaleBanner message="An input this proposal used has changed since it was computed." />
          <Link className="link" to={orderPath}>
            Run new analysis
          </Link>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="flex flex-col gap-6">
          <div>
            <h2 className="mb-2 text-base font-semibold">Rationale</h2>
            <p>{rec.rationale}</p>
          </div>

          <div>
            <h2 className="mb-2 text-base font-semibold">Evidence</h2>
            <EvidenceList evidence={rec.evidence.map(fromEvidenceOut)} factoryId={factory.id} />
          </div>

          <div>
            <h2 className="mb-2 text-base font-semibold">Decision</h2>
            {rec.decision ? (
              <p className="text-fg-muted">
                {rec.decision.decision === 'APPROVED' ? 'Approved' : 'Rejected'} by{' '}
                {rec.decision.decided_by.display_name} at {formatDateTime(rec.decision.decided_at, factory.timezone)}
                {rec.decision.reason && `. ${rec.decision.reason}`}
              </p>
            ) : (
              <DecisionForm
                recommendationId={rec.id}
                proposalHash={rec.proposal_hash}
                canDecide={rec.can_decide}
                blockedReason={rec.decide_blocked_reason}
                onDecided={() => {
                  void queryClient.invalidateQueries({ queryKey: ['recommendation', recId] })
                }}
              />
            )}
          </div>

          <div>
            <h2 className="mb-2 text-base font-semibold">Apply</h2>
            {rec.applied_at ? (
              <p className="text-fg-muted">Applied at {formatDateTime(rec.applied_at, factory.timezone)}.</p>
            ) : rec.can_apply ? (
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setConfirmApply(true)
                }}
              >
                <Icon name="check-double" />
                Apply
              </button>
            ) : (
              <p className="text-fg-muted">
                {rec.apply_blocked_reason
                  ? (BLOCKED_REASON_TEXT[rec.apply_blocked_reason] ?? rec.apply_blocked_reason)
                  : 'This recommendation cannot be applied.'}
              </p>
            )}
            {applyMutation.isError && !staleFieldErrors && (
              <div className="mt-2">
                <ErrorState title="The recommendation was not applied" error={applyMutation.error} />
              </div>
            )}
            {staleFieldErrors && staleFieldErrors.length > 0 && (
              <div className="mt-2 rounded-md border border-warn-line bg-warn-bg p-3 text-warn-fg">
                <p className="font-semibold">The inputs this proposal used have changed.</p>
                <StaleInputsList items={staleFieldErrors} />
              </div>
            )}
          </div>
        </section>

        <section>
          <h2 className="mb-3 text-base font-semibold">Proposed effect</h2>
          <DiffTables diff={rec.diff} />
        </section>
      </div>

      <ConfirmDialog
        open={confirmApply}
        title="Apply this recommendation?"
        confirmLabel="Apply"
        pending={applyMutation.isPending}
        onConfirm={() => {
          applyMutation.mutate(idempotency.keyFor({ recId, proposalHash: rec.proposal_hash }))
        }}
        onCancel={() => {
          setConfirmApply(false)
        }}
      >
        This allocates capacity and reserves materials for order {rec.order.external_ref}. It cannot be undone from
        here.
      </ConfirmDialog>
    </>
  )
}

export function RecommendationPage() {
  const { recId } = useParams<{ recId: string }>()
  if (!recId) return null
  return <RecommendationView recId={recId} />
}
