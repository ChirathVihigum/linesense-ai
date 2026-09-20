import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useMe } from '../../lib/auth'
import { humanizeCode } from '../../lib/format'
import { newIdempotencyKey } from '../../lib/idempotency'
import { ROLES } from '../../lib/permissions'

type Membership = Schemas['MembershipOut']
type RoleAssignment = Schemas['RoleAssignmentOut']

const MEMBERSHIPS_QUERY_KEY = ['admin-memberships'] as const

function GrantRoleForm({ membershipId, onGranted }: { membershipId: string; onGranted: () => void }) {
  const me = useMe()
  const roleId = useId()
  const factoryId = useId()
  const [role, setRole] = useState<string>(ROLES[1])
  const [factory, setFactory] = useState<string>('')

  const grant = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/admin/memberships/{membership_id}/roles', {
          params: {
            path: { membership_id: membershipId },
            header: { 'Idempotency-Key': newIdempotencyKey() },
          },
          body: { role, factory_id: factory || null },
        }),
      ),
    onSuccess: () => {
      onGranted()
    },
  })

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        grant.mutate()
      }}
    >
      <FormField id={roleId} label="Role">
        <select
          id={roleId}
          className="input h-8 w-auto py-0"
          value={role}
          onChange={(event) => {
            setRole(event.target.value)
          }}
        >
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {humanizeCode(value)}
            </option>
          ))}
        </select>
      </FormField>
      <FormField id={factoryId} label="Factory">
        <select
          id={factoryId}
          className="input h-8 w-auto py-0"
          value={factory}
          onChange={(event) => {
            setFactory(event.target.value)
          }}
        >
          <option value="">Organization-wide</option>
          {me.factories.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </FormField>
      <button type="submit" className="btn-secondary h-8" disabled={grant.isPending}>
        <Icon name="plus" />
        {grant.isPending ? 'Granting…' : 'Grant role'}
      </button>
      {grant.isError && <ErrorState error={grant.error} title="The role was not granted" />}
    </form>
  )
}

function RoleChip({
  assignment,
  onRevoke,
}: {
  assignment: RoleAssignment
  onRevoke: (assignment: RoleAssignment) => void
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-neutral-line bg-neutral-bg px-2 py-0.5 text-xs text-neutral-fg">
      {humanizeCode(assignment.role)} · {assignment.factory_code ?? 'Org-wide'}
      <button
        type="button"
        className="rounded-full p-0.5 hover:bg-surface-sunken"
        aria-label={`Revoke ${assignment.role} at ${assignment.factory_code ?? 'org-wide'}`}
        onClick={() => {
          onRevoke(assignment)
        }}
      >
        <Icon name="x" className="h-3 w-3" />
      </button>
    </span>
  )
}

function MembershipRow({
  membership,
  isSelf,
  onRevoke,
  onDeactivate,
  onGranted,
}: {
  membership: Membership
  isSelf: boolean
  onRevoke: (assignment: RoleAssignment) => void
  onDeactivate: (membership: Membership) => void
  onGranted: () => void
}) {
  return (
    <li className="panel flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium">{membership.display_name}</p>
          <p className="text-sm text-fg-muted">{membership.email}</p>
        </div>
        <div className="flex items-center gap-2">
          {!membership.is_active && (
            <span className="inline-flex h-6 items-center rounded-full border border-dashed border-neutral-line px-2 text-xs text-fg-muted">
              Inactive
            </span>
          )}
          <button
            type="button"
            className="btn-secondary h-8"
            disabled={isSelf || !membership.is_active}
            title={isSelf ? 'You cannot deactivate your own membership.' : undefined}
            onClick={() => {
              onDeactivate(membership)
            }}
          >
            Deactivate
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {membership.roles.length === 0 ? (
          <span className="text-xs text-fg-muted">No roles assigned.</span>
        ) : (
          membership.roles.map((assignment) => (
            <RoleChip key={assignment.id} assignment={assignment} onRevoke={onRevoke} />
          ))
        )}
      </div>
      {membership.is_active && <GrantRoleForm membershipId={membership.id} onGranted={onGranted} />}
    </li>
  )
}

export function MembershipTable() {
  const me = useMe()
  const queryClient = useQueryClient()
  const [revokeTarget, setRevokeTarget] = useState<RoleAssignment | null>(null)
  const [deactivateTarget, setDeactivateTarget] = useState<Membership | null>(null)

  const query = useQuery({
    queryKey: MEMBERSHIPS_QUERY_KEY,
    queryFn: () => unwrap(api.GET('/api/v1/admin/memberships', { params: { query: { limit: 200, offset: 0 } } })),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: MEMBERSHIPS_QUERY_KEY })

  const revoke = useMutation({
    mutationFn: (assignment: RoleAssignment) =>
      unwrap(
        api.DELETE('/api/v1/admin/role-assignments/{role_assignment_id}', {
          params: {
            path: { role_assignment_id: assignment.id },
            header: { 'Idempotency-Key': newIdempotencyKey() },
          },
        }),
      ),
    onSuccess: async () => {
      setRevokeTarget(null)
      await invalidate()
    },
  })

  const deactivate = useMutation({
    mutationFn: (membership: Membership) =>
      unwrap(
        api.POST('/api/v1/admin/memberships/{membership_id}/deactivate', {
          params: {
            path: { membership_id: membership.id },
            header: { 'Idempotency-Key': newIdempotencyKey() },
          },
        }),
      ),
    onSuccess: async () => {
      setDeactivateTarget(null)
      await invalidate()
    },
  })

  if (query.isPending) return <LoadingState label="Loading memberships…" variant="block" rows={4} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (query.data.items.length === 0) {
    return <EmptyState icon="users" title="No memberships" description="Organization members will appear here." />
  }

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3">
        {query.data.items.map((membership) => (
          <MembershipRow
            key={membership.id}
            membership={membership}
            isSelf={membership.user_id === me.user.id}
            onRevoke={setRevokeTarget}
            onDeactivate={setDeactivateTarget}
            onGranted={() => void invalidate()}
          />
        ))}
      </ul>

      <ConfirmDialog
        open={revokeTarget !== null}
        title={revokeTarget ? `Revoke ${humanizeCode(revokeTarget.role)}?` : ''}
        confirmLabel={revoke.isPending ? 'Revoking…' : 'Revoke role'}
        pending={revoke.isPending}
        onCancel={() => {
          setRevokeTarget(null)
        }}
        onConfirm={() => {
          if (revokeTarget) revoke.mutate(revokeTarget)
        }}
      >
        {revoke.isError && <ErrorState error={revoke.error} title="The role was not revoked" />}
        <p>
          This removes {revokeTarget ? humanizeCode(revokeTarget.role) : ''} at{' '}
          {revokeTarget?.factory_code ?? 'org-wide'} and signs the user out of every active session.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={deactivateTarget !== null}
        title={deactivateTarget ? `Deactivate ${deactivateTarget.display_name}?` : ''}
        confirmLabel={deactivate.isPending ? 'Deactivating…' : 'Deactivate'}
        pending={deactivate.isPending}
        onCancel={() => {
          setDeactivateTarget(null)
        }}
        onConfirm={() => {
          if (deactivateTarget) deactivate.mutate(deactivateTarget)
        }}
      >
        {deactivate.isError && <ErrorState error={deactivate.error} title="The membership was not deactivated" />}
        <p>This signs the user out of every active session and removes their access to the organization.</p>
      </ConfirmDialog>
    </div>
  )
}
