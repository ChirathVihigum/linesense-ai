import { Link } from 'react-router'

import { Icon } from '../../components/Icon'
import { toApiError } from '../../lib/api'
import { useLogout, useMe } from '../../lib/auth'

export function NoAccessPage() {
  const me = useMe()
  const logout = useLogout()
  return (
    <main id="main-content" className="mx-auto max-w-xl p-8">
      <h1 className="text-xl font-semibold tracking-tight">No factory access</h1>
      <p className="mt-2 max-w-[65ch] text-fg-muted">
        You are signed in as {me.user.display_name} ({me.user.email}), but your account has no role
        in any factory of {me.organization.name}. Ask an organization administrator to grant you
        access.
      </p>
      {me.factories.length > 0 && (
        <Link className="link mt-4 inline-block" to="/">
          Continue to your factory
        </Link>
      )}
      {logout.isError && (
        <p role="alert" className="mt-3 text-bad-fg">
          Sign out failed: {toApiError(logout.error).message}
        </p>
      )}
      <button
        type="button"
        className="btn-secondary mt-6"
        disabled={logout.isPending}
        onClick={() => {
          logout.mutate()
        }}
      >
        <Icon name="sign-out" />
        Sign out
      </button>
    </main>
  )
}
