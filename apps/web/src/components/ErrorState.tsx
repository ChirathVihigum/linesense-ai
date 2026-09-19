import { toApiError } from '../lib/api'
import { Icon } from './Icon'

/** A failed request: the server's message, its trace id (for support) and a retry action. */
export function ErrorState({
  error,
  onRetry,
  title = 'Something went wrong',
}: {
  error: unknown
  onRetry?: () => void
  title?: string
}) {
  const apiError = toApiError(error)
  return (
    <div role="alert" className="rounded-md border border-bad-line bg-bad-bg p-4 text-bad-fg">
      <div className="flex items-center gap-2 font-semibold">
        <Icon name="x-circle" />
        <span>{title}</span>
      </div>
      <p className="mt-1">{apiError.message}</p>
      {apiError.traceId && (
        <p className="mt-2 text-xs">
          Trace ID <code className="font-mono select-all">{apiError.traceId}</code>
        </p>
      )}
      {apiError.retryAfterSeconds !== null && (
        <p className="mt-1 text-xs">Try again in {apiError.retryAfterSeconds} seconds.</p>
      )}
      {onRetry && (
        <button type="button" className="btn-secondary mt-3" onClick={onRetry}>
          <Icon name="refresh" />
          Retry
        </button>
      )}
    </div>
  )
}
