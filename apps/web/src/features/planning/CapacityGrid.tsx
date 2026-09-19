import { Link } from 'react-router'

import { Icon } from '../../components/Icon'
import type { Schemas } from '../../lib/api'
import { formatDate, formatDecimal, formatPercent } from '../../lib/format'

type CapacityBoard = Schemas['CapacityBoardOut']
type BoardLine = Schemas['BoardLineOut']
type Slot = Schemas['SlotOut']

/** A slot at or above this utilization is flagged (icon + text, never colour alone). */
export const HIGH_UTILIZATION_THRESHOLD = 0.95
const SHIFT_CODES = ['A', 'B'] as const

function dateRange(start: string, end: string): string[] {
  const dates: string[] = []
  const cursor = new Date(`${start}T00:00:00Z`)
  const last = new Date(`${end}T00:00:00Z`)
  while (cursor <= last) {
    dates.push(cursor.toISOString().slice(0, 10))
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
  return dates
}

function AllocationChip({ factoryCode, orderId, externalRef }: { factoryCode: string; orderId: string; externalRef: string }) {
  return (
    <Link
      key={orderId}
      to={`/f/${encodeURIComponent(factoryCode)}/orders?q=${encodeURIComponent(externalRef)}`}
      className="inline-flex h-6 items-center rounded-full border border-line-strong/60 bg-surface px-2 font-mono text-xs font-medium text-fg hover:bg-surface-sunken"
    >
      {externalRef}
    </Link>
  )
}

function SlotCell({ slot, factoryCode }: { slot: Slot; factoryCode: string }) {
  const utilization = slot.utilization === null ? null : Number(slot.utilization)
  const flagged = utilization !== null && utilization >= HIGH_UTILIZATION_THRESHOLD
  const barWidth = utilization === null ? 0 : Math.min(100, Math.max(0, utilization * 100))

  return (
    <div className="flex min-w-40 flex-col gap-1.5 py-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="tabular-nums text-fg-muted">
          {formatDecimal(slot.allocated_standard_minutes, 0)} / {formatDecimal(slot.capacity_standard_minutes, 0)} min
        </span>
        <span className="font-medium tabular-nums">{formatPercent(utilization)}</span>
      </div>
      <div
        role="img"
        aria-label={`Utilization ${formatPercent(utilization)}`}
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken"
      >
        <div
          aria-hidden="true"
          className={flagged ? 'h-full rounded-full bg-bad-fg' : 'h-full rounded-full bg-accent'}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      {flagged && (
        <span className="inline-flex items-center gap-1 text-xs font-medium text-bad-fg">
          <Icon name="alert" className="h-3.5 w-3.5" />
          High utilization
        </span>
      )}
      <p className="text-xs text-fg-muted tabular-nums">
        Remaining {formatDecimal(slot.remaining_standard_minutes, 0)} min
      </p>
      {slot.allocations.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {slot.allocations.map((allocation) => (
            <AllocationChip
              key={allocation.id}
              factoryCode={factoryCode}
              orderId={allocation.order_id}
              externalRef={allocation.order_external_ref}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function LineGrid({ line, dates, factoryCode }: { line: BoardLine; dates: string[]; factoryCode: string }) {
  const slotsByDateShift = new Map<string, Slot>()
  for (const slot of line.slots) slotsByDateShift.set(`${slot.slot_date}|${slot.shift_code}`, slot)

  return (
    <div className="panel overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <caption className="sr-only">
          Capacity for {line.name} ({line.code}), {formatDate(dates[0])} to {formatDate(dates[dates.length - 1])}
        </caption>
        <thead className="bg-surface-sunken">
          <tr>
            <th scope="col" className="px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-fg-muted">
              Shift
            </th>
            {dates.map((date) => (
              <th key={date} scope="col" className="px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-fg-muted">
                {formatDate(date)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {SHIFT_CODES.map((shift) => (
            <tr key={shift}>
              <th scope="row" className="px-3 py-2 text-left align-top font-medium whitespace-nowrap">
                Shift {shift}
              </th>
              {dates.map((date) => {
                const slot = slotsByDateShift.get(`${date}|${shift}`)
                return (
                  <td key={date} className="px-3 py-2 align-top">
                    {slot ? (
                      <SlotCell slot={slot} factoryCode={factoryCode} />
                    ) : (
                      <span className="text-xs text-fg-muted">No slot</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Capacity board: one scrollable table per line, dates across, shifts A/B
 * down. Every slot shows capacity/allocated/remaining, a utilization bar
 * with numeric text, a high-utilization flag (icon + text, DESIGN.md "status
 * is never colour alone") at 95% and above, and allocation chips linking to
 * their order in the orders list.
 */
export function CapacityGrid({ board, factoryCode }: { board: CapacityBoard; factoryCode: string }) {
  const dates = dateRange(board.start, board.end)
  return (
    <div className="flex flex-col gap-6">
      {board.lines.map((line) => (
        <section key={line.id} aria-label={`${line.name} capacity`} className="flex flex-col gap-2">
          <h3 className="text-base font-semibold">
            {line.name} <span className="font-mono text-xs font-normal text-fg-muted">{line.code}</span>
          </h3>
          <LineGrid line={line} dates={dates} factoryCode={factoryCode} />
        </section>
      ))}
    </div>
  )
}
