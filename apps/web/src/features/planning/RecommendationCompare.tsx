import { Icon } from '../../components/Icon'
import { formatDate, formatInteger } from '../../lib/format'

/**
 * A PROPOSED planning recommendation, shaped for comparison against the live
 * board. NOT wired into `PlanningBoardPage` yet: the recommendation routes
 * (`GET .../recommendations`, Task 14) do not exist in the backend or in
 * `src/generated/api.ts` as of Task 23. Per the brief, the compare panel is
 * rendered "only when the route exists in the generated types"; since it
 * does not, `PlanningBoardPage` omits the panel rather than call a route
 * that would not compile, or show placeholder/mock data. This component is
 * kept ready (typed against its own props, not the generated client) so the
 * planning board can wire it in with no redesign once Task 14 lands.
 */
export interface PlanningRecommendationSummary {
  id: string
  orderId: string
  orderExternalRef: string
  allocatedUnits: number
  unscheduledUnits: number
  projectedFinishDate: string | null
  linesUsed: string[]
  /** True when an input (order, capacity slot, material) changed since the recommendation was proposed. */
  isStale: boolean
  reviewPath: string
}

export function RecommendationCompare({
  recommendations,
}: {
  recommendations: PlanningRecommendationSummary[]
}) {
  if (recommendations.length === 0) {
    return <p className="text-fg-muted">No proposed recommendations to compare.</p>
  }
  return (
    <ul aria-label="Proposed recommendations" className="flex flex-col gap-3">
      {recommendations.map((recommendation) => (
        <li key={recommendation.id} className="panel flex flex-col gap-2 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs font-medium">{recommendation.orderExternalRef}</span>
            {recommendation.isStale && (
              <span className="inline-flex items-center gap-1 rounded-full border border-warn-line bg-warn-bg px-2 py-0.5 text-xs font-medium text-warn-fg">
                <Icon name="clock" className="h-3.5 w-3.5" />
                Stale
              </span>
            )}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-fg-muted">Allocated units</dt>
            <dd className="text-right tabular-nums">{formatInteger(recommendation.allocatedUnits)}</dd>
            <dt className="text-fg-muted">Unscheduled units</dt>
            <dd className="text-right tabular-nums">{formatInteger(recommendation.unscheduledUnits)}</dd>
            <dt className="text-fg-muted">Projected finish</dt>
            <dd className="text-right">{formatDate(recommendation.projectedFinishDate)}</dd>
            <dt className="text-fg-muted">Lines used</dt>
            <dd className="text-right">{recommendation.linesUsed.join(', ') || 'None'}</dd>
          </dl>
          <a className="link self-start" href={recommendation.reviewPath}>
            Review recommendation
          </a>
        </li>
      ))}
    </ul>
  )
}
