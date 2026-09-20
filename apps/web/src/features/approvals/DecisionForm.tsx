import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { fieldAria } from '../../components/fieldAria'
import { api, unwrap } from '../../lib/api'
import { BLOCKED_REASON_TEXT } from './blockedReasons'

const REASON_MIN = 3
const REASON_MAX = 500

type Decision = 'APPROVED' | 'REJECTED'

/** Approve/Reject for a PROPOSED recommendation. Reject requires a reason (3 to 500
 * characters); Approve's reason is optional. Each sends the `proposal_hash` the page
 * loaded, so a proposal that changed underneath the reviewer is rejected with 409. */
export function DecisionForm({
  recommendationId,
  proposalHash,
  canDecide,
  blockedReason,
  onDecided,
}: {
  recommendationId: string
  proposalHash: string
  canDecide: boolean
  blockedReason: string | null
  onDecided: () => void
}) {
  const [pending, setPending] = useState<Decision | null>(null)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | undefined>()

  const mutation = useMutation({
    mutationFn: (decision: Decision) =>
      unwrap(
        api.POST('/api/v1/recommendations/{rec_id}/decision', {
          params: { path: { rec_id: recommendationId } },
          body: { decision, proposal_hash: proposalHash, reason: reason.trim() || null },
        }),
      ),
    onSuccess: () => {
      setPending(null)
      setReason('')
      setReasonError(undefined)
      onDecided()
    },
  })

  if (!canDecide) {
    return (
      <p className="text-fg-muted">
        {blockedReason ? (BLOCKED_REASON_TEXT[blockedReason] ?? blockedReason) : 'This recommendation cannot be decided.'}
      </p>
    )
  }

  const openDialog = (decision: Decision) => {
    setReason('')
    setReasonError(undefined)
    setPending(decision)
  }

  const confirm = () => {
    if (pending === 'REJECTED') {
      const trimmed = reason.trim()
      if (trimmed.length < REASON_MIN || trimmed.length > REASON_MAX) {
        setReasonError(`Enter a reason of ${REASON_MIN} to ${REASON_MAX} characters.`)
        return
      }
    }
    if (pending) mutation.mutate(pending)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            openDialog('APPROVED')
          }}
        >
          <Icon name="check" />
          Approve
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            openDialog('REJECTED')
          }}
        >
          <Icon name="x" />
          Reject
        </button>
      </div>
      {mutation.isError && <ErrorState title="The decision was not recorded" error={mutation.error} />}

      <ConfirmDialog
        open={pending !== null}
        title={pending === 'REJECTED' ? 'Reject this recommendation?' : 'Approve this recommendation?'}
        confirmLabel={pending === 'REJECTED' ? 'Reject' : 'Approve'}
        pending={mutation.isPending}
        onConfirm={confirm}
        onCancel={() => {
          setPending(null)
        }}
      >
        <div className="flex flex-col gap-3 text-left">
          <p>
            {pending === 'REJECTED'
              ? 'The proposer is notified and this recommendation cannot be applied.'
              : 'This does not apply the change; a supervisor must still apply it.'}
          </p>
          <FormField
            id="decision-reason"
            label="Reason"
            hint={pending === 'REJECTED' ? 'Required, 3 to 500 characters.' : 'Optional, up to 500 characters.'}
            error={reasonError}
            required={pending === 'REJECTED'}
          >
            <textarea
              className="input"
              rows={3}
              maxLength={REASON_MAX}
              value={reason}
              {...fieldAria('decision-reason', reasonError, 'decision-reason-hint', pending === 'REJECTED')}
              onChange={(event) => {
                setReason(event.target.value)
              }}
            />
          </FormField>
        </div>
      </ConfirmDialog>
    </div>
  )
}
