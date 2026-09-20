import type { Schemas } from '../../lib/api'
import { formatDate, formatDecimal, formatPercent } from '../../lib/format'

type SlotDiff = Schemas['SlotDiffOut']
type ReservationDiff = Schemas['ReservationDiffOut']

function UtilizationBar({ value }: { value: string | null }) {
  const fraction = value === null ? null : Number(value)
  const pct = fraction === null || Number.isNaN(fraction) ? 0 : Math.min(100, Math.max(0, fraction * 100))
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 overflow-hidden rounded-full bg-surface-sunken" aria-hidden="true">
        <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums">{formatPercent(fraction === null || Number.isNaN(fraction) ? null : fraction)}</span>
    </div>
  )
}

function SlotDiffTable({ slots }: { slots: SlotDiff[] }) {
  if (slots.length === 0) return <p className="text-fg-muted">No capacity slots in this proposal.</p>
  return (
    <div className="panel overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <caption className="sr-only">Capacity slot changes</caption>
        <thead className="bg-surface-sunken">
          <tr>
            {['Line', 'Date', 'Shift', 'Capacity', 'Before', 'After', 'Remaining', 'Utilization'].map((header) => (
              <th key={header} scope="col" className="px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-fg-muted">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {slots.map((slot) => (
            <tr key={slot.slot_id}>
              <td className="px-3 py-2">{slot.line_code}</td>
              <td className="px-3 py-2 whitespace-nowrap">{formatDate(slot.slot_date)}</td>
              <td className="px-3 py-2">{slot.shift_code}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(slot.capacity)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(slot.allocated_before)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(slot.allocated_after)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(slot.remaining_after)}</td>
              <td className="px-3 py-2">
                <UtilizationBar value={slot.utilization_after} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ReservationDiffTable({ reservations }: { reservations: ReservationDiff[] }) {
  if (reservations.length === 0) return <p className="text-fg-muted">No material reservations in this proposal.</p>
  return (
    <div className="panel overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <caption className="sr-only">Reservation changes</caption>
        <thead className="bg-surface-sunken">
          <tr>
            {['Material', 'Quantity', 'On hand', 'Reserved before', 'Reserved after', 'Available after'].map(
              (header) => (
                <th
                  key={header}
                  scope="col"
                  className="px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-fg-muted"
                >
                  {header}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {reservations.map((reservation) => (
            <tr key={reservation.material_id}>
              <td className="px-3 py-2">
                {reservation.material_code} <span className="text-xs text-fg-muted">({reservation.unit})</span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(reservation.quantity)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(reservation.on_hand)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(reservation.reserved_before)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(reservation.reserved_after)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{formatDecimal(reservation.available_after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The recommendation's proposed effect on capacity slots and material reservations. */
export function DiffTables({ diff }: { diff: Schemas['ProposalDiff'] }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="mb-2 text-sm font-semibold">Capacity</h3>
        <SlotDiffTable slots={diff.slots} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Materials</h3>
        <ReservationDiffTable reservations={diff.reservations} />
      </div>
    </div>
  )
}
