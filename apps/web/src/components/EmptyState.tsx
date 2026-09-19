import type { ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

export function EmptyState({
  title,
  description,
  action,
  icon = 'info',
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: IconName
}) {
  return (
    <div className="panel flex flex-col items-start gap-2 border-dashed px-6 py-10">
      <Icon name={icon} className="h-6 w-6 text-fg-muted" />
      <p className="text-base font-semibold">{title}</p>
      {description && <p className="max-w-[65ch] text-fg-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
