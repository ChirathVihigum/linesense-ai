import { Icon } from './Icon'

/** The shipment-eligibility pill (icon + text + colour, never colour alone). Labels are
 * parameterised: different screens phrase the same boolean slightly differently
 * ("Eligible" vs "Eligible for shipment" vs "Shipment eligible"). */
export function ShipmentEligibilityBadge({
  eligible,
  eligibleLabel = 'Eligible for shipment',
  ineligibleLabel = 'Not eligible for shipment',
}: {
  eligible: boolean
  eligibleLabel?: string
  ineligibleLabel?: string
}) {
  return (
    <span
      className={
        eligible
          ? 'inline-flex h-6 w-fit items-center gap-1 rounded-full border border-ok-line bg-ok-bg px-2 text-xs font-medium text-ok-fg'
          : 'inline-flex h-6 w-fit items-center gap-1 rounded-full border border-neutral-line bg-neutral-bg px-2 text-xs font-medium text-neutral-fg'
      }
    >
      <Icon name={eligible ? 'truck' : 'ban'} className="h-3.5 w-3.5" />
      {eligible ? eligibleLabel : ineligibleLabel}
    </span>
  )
}
