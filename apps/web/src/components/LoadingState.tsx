/**
 * Loading placeholders shaped like the content they stand in for (skeletons,
 * not spinners). The label is announced to screen readers.
 */
export function LoadingState({
  label = 'Loading…',
  variant = 'block',
  rows = 6,
}: {
  label?: string
  variant?: 'block' | 'table' | 'page'
  rows?: number
}) {
  if (variant === 'page') {
    return (
      <div role="status" className="flex min-h-[100dvh] items-center justify-center">
        <div className="flex w-64 flex-col gap-3" aria-hidden="true">
          <div className="skeleton h-5 w-32" />
          <div className="skeleton h-3 w-full" />
          <div className="skeleton h-3 w-3/4" />
        </div>
        <span className="sr-only">{label}</span>
      </div>
    )
  }
  if (variant === 'table') {
    return (
      <div role="status" className="panel overflow-hidden">
        <div className="h-10 border-b border-line bg-surface-sunken" aria-hidden="true" />
        <div className="divide-y divide-line" aria-hidden="true">
          {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="grid grid-cols-[1fr_2fr_1fr_1fr_1fr] gap-4 px-3 py-3">
              <div className="skeleton h-3" />
              <div className="skeleton h-3" />
              <div className="skeleton h-3" />
              <div className="skeleton h-5 w-20 rounded-full" />
              <div className="skeleton h-5 w-20 rounded-full" />
            </div>
          ))}
        </div>
        <span className="sr-only">{label}</span>
      </div>
    )
  }
  return (
    <div role="status" className="flex flex-col gap-3 py-6">
      <div className="skeleton h-4 w-1/3" aria-hidden="true" />
      <div className="skeleton h-3 w-2/3" aria-hidden="true" />
      <div className="skeleton h-3 w-1/2" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </div>
  )
}
