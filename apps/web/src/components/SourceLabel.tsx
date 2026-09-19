import clsx from 'clsx'

import { formatDateTime } from '../lib/format'
import { Icon, type IconName } from './Icon'

/** Where a value or piece of text came from. Shown next to anything a user might act on. */
export type Source =
  | { kind: 'calculated' }
  | { kind: 'ai_recommendation' }
  | { kind: 'test_fixture' }
  | { kind: 'ai_unavailable' }
  | { kind: 'pending_approval' }
  | { kind: 'approved'; approvedBy: string; approvedAt: string; timeZone: string }

const STYLES: Record<Source['kind'], { icon: IconName; className: string }> = {
  calculated: { icon: 'calculator', className: 'border-neutral-line bg-neutral-bg text-neutral-fg' },
  ai_recommendation: { icon: 'sparkle', className: 'border-info-line bg-info-bg text-info-fg' },
  test_fixture: { icon: 'beaker', className: 'border-warn-line bg-warn-bg text-warn-fg' },
  ai_unavailable: { icon: 'alert', className: 'border-dashed border-neutral-line text-fg-muted' },
  pending_approval: { icon: 'clock', className: 'border-warn-line bg-warn-bg text-warn-fg' },
  approved: { icon: 'user-check', className: 'border-ok-line bg-ok-bg text-ok-fg' },
}

function sourceText(source: Source): string {
  switch (source.kind) {
    case 'calculated':
      return 'Calculated from records'
    case 'ai_recommendation':
      return 'AI recommendation'
    case 'test_fixture':
      return 'Test fixture — not a live AI model'
    case 'ai_unavailable':
      return 'AI explanation unavailable'
    case 'pending_approval':
      return 'Pending human approval'
    case 'approved':
      return `Approved by ${source.approvedBy} at ${formatDateTime(source.approvedAt, source.timeZone)}`
  }
}

export function SourceLabel({ source }: { source: Source }) {
  const style = STYLES[source.kind]
  return (
    <span
      data-source={source.kind}
      className={clsx(
        'inline-flex h-6 items-center gap-1 rounded-md border px-2 text-xs font-medium',
        style.className,
      )}
    >
      <Icon name={style.icon} className="h-3.5 w-3.5 shrink-0" />
      <span>{sourceText(source)}</span>
    </span>
  )
}
