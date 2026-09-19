import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { fieldAria } from '../../components/fieldAria'
import { ApiError, api, unwrap } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { CapacityGrid } from './CapacityGrid'
import { addDaysIso, isoDateInTimeZone, rangeError } from './dateRange'

function PlanningBoard() {
  const factory = useFactory()
  const [start, setStart] = useState(() => isoDateInTimeZone(factory.timezone))
  const [end, setEnd] = useState(() => addDaysIso(isoDateInTimeZone(factory.timezone), 13))
  const error = rangeError(start, end)

  const query = useQuery({
    queryKey: ['capacity-board', factory.id, start, end],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/capacity', {
          params: { path: { factory_id: factory.id }, query: { start, end } },
        }),
      ),
    enabled: !error,
  })

  let body
  if (error) {
    body = null
  } else if (query.isPending) {
    body = <LoadingState label="Loading the capacity board…" variant="table" />
  } else if (query.isError) {
    body =
      query.error instanceof ApiError && query.error.status === 403 ? (
        <PermissionDenied />
      ) : (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      )
  } else if (query.data.lines.length === 0) {
    body = (
      <EmptyState
        icon="factory"
        title="No production lines"
        description="Add production lines for this factory to see the capacity board."
      />
    )
  } else {
    body = <CapacityGrid board={query.data} factoryCode={factory.code} />
  }

  return (
    <div className="flex flex-col gap-6">
      <form
        role="search"
        aria-label="Date range"
        className="grid max-w-md grid-cols-2 gap-3"
        onSubmit={(event) => {
          event.preventDefault()
        }}
      >
        <FormField id="planning-start" label="Start date" required error={error && !end ? error : undefined}>
          <input
            type="date"
            className="input"
            value={start}
            {...fieldAria('planning-start', undefined, undefined, true)}
            onChange={(event) => {
              setStart(event.target.value)
            }}
          />
        </FormField>
        <FormField id="planning-end" label="End date" required error={error}>
          <input
            type="date"
            className="input"
            value={end}
            {...fieldAria('planning-end', error, undefined, true)}
            onChange={(event) => {
              setEnd(event.target.value)
            }}
          />
        </FormField>
      </form>
      {/*
       * Compare PROPOSED recommendations panel (see RecommendationCompare.tsx): omitted here.
       * The recommendation routes it needs (GET .../recommendations, Task 14) do not exist yet
       * in the backend or in src/generated/api.ts, so there is nothing real to fetch. Per the
       * brief this panel is shown only once the route exists in the generated types; until then
       * no placeholder or mock data is rendered.
       */}
      <div aria-busy={query.isFetching}>{body}</div>
    </div>
  )
}

export function PlanningBoardPage() {
  const can = useCan()
  return (
    <>
      <PageHeader title="Planning board" description="Line capacity, allocation and utilization by date and shift." />
      {can('capacity:read') ? (
        <PlanningBoard />
      ) : (
        <PermissionDenied message="Your role does not include access to the planning board." />
      )}
    </>
  )
}
