import { useQueries, useQuery } from '@tanstack/react-query'

import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { api, unwrap } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { RecommendationCompare, type PlanningRecommendationSummary } from './RecommendationCompare'

/** Recommendation kinds that carry a capacity allocation (worth comparing against the board). */
const ALLOCATION_KINDS = new Set(['ALLOCATION', 'ALLOCATION_AND_RESERVATION'])

/**
 * Every allocation-carrying recommendation currently PROPOSED for the factory,
 * compared against the live board. The list route
 * (`GET /factories/{id}/recommendations`) landed in Task 14; this section
 * fetches each candidate's detail (bounded to the list's page) for the slot
 * diff, since the list summary does not include the proposal.
 */
export function RecommendationCompareSection() {
  const factory = useFactory()

  const listQuery = useQuery({
    queryKey: ['recommendations', factory.id, 'PROPOSED', 'compare'],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/recommendations', {
          params: { path: { factory_id: factory.id }, query: { status: 'PROPOSED', limit: 20, offset: 0 } },
        }),
      ),
  })

  const candidates = (listQuery.data?.items ?? []).filter((item) => ALLOCATION_KINDS.has(item.kind))

  const detailQueries = useQueries({
    queries: candidates.map((item) => ({
      queryKey: ['recommendation', item.id],
      queryFn: () => unwrap(api.GET('/api/v1/recommendations/{rec_id}', { params: { path: { rec_id: item.id } } })),
      enabled: listQuery.isSuccess,
    })),
  })

  if (listQuery.isPending) return <LoadingState label="Loading proposed recommendations…" />
  if (listQuery.isError) {
    return <ErrorState error={listQuery.error} onRetry={() => void listQuery.refetch()} />
  }

  const recommendations: PlanningRecommendationSummary[] = detailQueries
    .map((q) => q.data)
    .filter((data): data is NonNullable<typeof data> => data !== undefined)
    .map((data) => {
      const proposal = data.proposal as Record<string, unknown>
      const allocatedUnits = data.diff.slots.reduce((sum, slot) => sum + Number(slot.units), 0)
      const linesUsed = [...new Set(data.diff.slots.map((slot) => slot.line_code))]
      const unscheduledUnits =
        typeof proposal.unscheduled_units === 'number' ? proposal.unscheduled_units : 0
      const projectedFinishDate =
        typeof proposal.projected_finish_date === 'string' ? proposal.projected_finish_date : null
      return {
        id: data.id,
        orderId: data.order.id,
        orderExternalRef: data.order.external_ref,
        allocatedUnits,
        unscheduledUnits,
        projectedFinishDate,
        linesUsed,
        isStale: data.stale,
        reviewPath: `/f/${encodeURIComponent(factory.code)}/approvals/${data.id}`,
      }
    })

  return <RecommendationCompare recommendations={recommendations} />
}
