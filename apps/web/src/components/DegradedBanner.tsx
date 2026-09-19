import { Icon } from './Icon'

/** A result produced with reduced capability (e.g. an analysis without AI explanations). */
export function DegradedBanner({ message }: { message: string }) {
  return (
    <div
      role="status"
      className="flex items-center gap-3 rounded-md border border-warn-line bg-warn-bg px-4 py-2 text-warn-fg"
    >
      <Icon name="alert" />
      <span className="font-semibold">Degraded result.</span>
      <span>{message}</span>
    </div>
  )
}
