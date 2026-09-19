import { Icon } from './Icon'

export function PermissionDenied({
  message = 'Your role in this factory does not include access to this page.',
}: {
  message?: string
}) {
  return (
    <div role="alert" className="panel flex gap-3 p-4">
      <Icon name="lock" className="mt-0.5 h-5 w-5 shrink-0 text-fg-muted" />
      <div>
        <p className="font-semibold">Permission required</p>
        <p className="mt-1 text-fg-muted">{message}</p>
      </div>
    </div>
  )
}
