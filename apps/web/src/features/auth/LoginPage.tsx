import { useSearchParams } from 'react-router'

import { Icon } from '../../components/Icon'
import { AuthCallbackNotice } from './AuthCallbackNotice'
import { safeNextPath } from './safeNextPath'

export function LoginPage() {
  const [searchParams] = useSearchParams()
  const next = safeNextPath(searchParams.get('next'))
  const error = searchParams.get('error')

  return (
    <main id="main-content" className="flex min-h-[100dvh] items-center justify-center p-4">
      <div className="panel w-full max-w-sm p-8">
        <div className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Icon name="factory" className="h-6 w-6 text-accent" />
          <h1>LineSense AI</h1>
        </div>
        <p className="mt-2 text-fg-muted">
          Sign in with your organization account to plan orders, materials and quality.
        </p>
        <div className="mt-4">
          <AuthCallbackNotice error={error} />
        </div>
        <a
          className="btn-primary mt-6 w-full justify-center"
          href={`/auth/login?next=${encodeURIComponent(next)}`}
        >
          Sign in
        </a>
      </div>
    </main>
  )
}
