import { formatInteger } from '../lib/format'

/** Server pagination for `{items,total,limit,offset}` collections. */
export function Pagination({
  total,
  limit,
  offset,
  onOffsetChange,
}: {
  total: number
  limit: number
  offset: number
  onOffsetChange: (offset: number) => void
}) {
  if (total === 0) return null
  const first = offset + 1
  const last = Math.min(offset + limit, total)
  const hasPrevious = offset > 0
  const hasNext = offset + limit < total
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between gap-3 text-sm">
      <p className="text-fg-muted tabular-nums">
        Showing {formatInteger(first)}-{formatInteger(last)} of {formatInteger(total)}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn-secondary"
          disabled={!hasPrevious}
          onClick={() => {
            onOffsetChange(Math.max(0, offset - limit))
          }}
        >
          Previous page
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!hasNext}
          onClick={() => {
            onOffsetChange(offset + limit)
          }}
        >
          Next page
        </button>
      </div>
    </nav>
  )
}
