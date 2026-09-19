import { Icon } from './Icon'

/** Data on screen may be out of date (e.g. inputs changed since it was computed). */
export function StaleBanner({ message, onRefresh }: { message: string; onRefresh?: () => void }) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-md border border-warn-line bg-warn-bg px-4 py-2 text-warn-fg"
    >
      <Icon name="clock" />
      <span className="font-semibold">Possibly out of date.</span>
      <span>{message}</span>
      {onRefresh && (
        <button type="button" className="btn-secondary ml-auto" onClick={onRefresh}>
          <Icon name="refresh" />
          Refresh
        </button>
      )}
    </div>
  )
}
