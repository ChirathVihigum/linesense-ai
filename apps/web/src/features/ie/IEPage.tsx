import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { ApiError, api, fetchAllPages, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { formatDecimal } from '../../lib/format'
import { BottleneckChart } from './BottleneckChart'
import { ObservationForm } from './ObservationForm'
import { SampleTable } from './SampleTable'

function useLines(factoryId: string) {
  return useQuery({
    queryKey: ['lines', factoryId],
    queryFn: () =>
      fetchAllPages<Schemas['LineOut']>((offset, limit) =>
        unwrap(api.GET('/api/v1/factories/{factory_id}/lines', { params: { path: { factory_id: factoryId }, query: { offset, limit } } })),
      ),
    staleTime: 60_000,
  })
}

function useStyles(factoryId: string) {
  return useQuery({
    queryKey: ['styles', factoryId],
    queryFn: () =>
      fetchAllPages<Schemas['StyleOut']>((offset, limit) =>
        unwrap(api.GET('/api/v1/factories/{factory_id}/styles', { params: { path: { factory_id: factoryId }, query: { offset, limit } } })),
      ),
    staleTime: 60_000,
  })
}

function LineStyleSelectors({
  lineId,
  styleId,
  onLineChange,
  onStyleChange,
  lines,
  styles,
}: {
  lineId: string
  styleId: string
  onLineChange: (id: string) => void
  onStyleChange: (id: string) => void
  lines: Schemas['LineOut'][]
  styles: Schemas['StyleOut'][]
}) {
  return (
    <form role="search" aria-label="Line and style" className="mb-6 grid max-w-xl grid-cols-2 gap-3">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="ie-line" className="text-xs font-medium text-fg-muted">
          Line
        </label>
        <select
          id="ie-line"
          className="input"
          value={lineId}
          onChange={(event) => {
            onLineChange(event.target.value)
          }}
        >
          <option value="">Select a line</option>
          {lines.map((line) => (
            <option key={line.id} value={line.id}>
              {line.name} ({line.code})
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="ie-style" className="text-xs font-medium text-fg-muted">
          Style
        </label>
        <select
          id="ie-style"
          className="input"
          value={styleId}
          onChange={(event) => {
            onStyleChange(event.target.value)
          }}
        >
          <option value="">Select a style</option>
          {styles.map((style) => (
            <option key={style.id} value={style.id}>
              {style.name} ({style.code})
            </option>
          ))}
        </select>
      </div>
    </form>
  )
}

function Analysis({ factoryId, lineId, styleId }: { factoryId: string; lineId: string; styleId: string }) {
  const can = useCan()
  const query = useQuery({
    queryKey: ['ie-analysis', factoryId, lineId, styleId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/ie/lines/{line_id}/styles/{style_id}/analysis', {
          params: { path: { factory_id: factoryId, line_id: lineId, style_id: styleId } },
        }),
      ),
  })

  if (query.isPending) return <LoadingState label="Loading the line balance…" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }
  const analysis = query.data
  if (analysis.operations.length === 0) {
    return <EmptyState icon="calculator" title="No operations" description="This style has no defined operations." />
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="panel p-4">
          <p className="text-xs font-medium text-fg-muted">Observed units/hour</p>
          <p className="text-xl font-semibold tabular-nums">{formatDecimal(analysis.observed_units_per_hour, 1)}</p>
        </div>
        <div className="panel p-4">
          <p className="text-xs font-medium text-fg-muted">SAM units/hour</p>
          <p className="text-xl font-semibold tabular-nums">{formatDecimal(analysis.sam_units_per_hour, 1)}</p>
        </div>
      </div>

      <BottleneckChart operations={analysis.operations} balance={analysis.balance} />
      <SampleTable operations={analysis.operations} balance={analysis.balance} />

      {(analysis.assumptions.length > 0 || analysis.limitations.length > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {analysis.assumptions.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Assumptions</h3>
              <ul className="list-disc pl-5 text-sm text-fg-muted">
                {analysis.assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {analysis.limitations.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Limitations</h3>
              <ul className="list-disc pl-5 text-sm text-fg-muted">
                {analysis.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {can('ie:write') && (
        <section aria-labelledby="ie-observation" className="panel p-6">
          <h2 id="ie-observation" className="mb-4 text-base font-semibold">
            Record a cycle observation
          </h2>
          <ObservationForm factoryId={factoryId} lineId={lineId} styleId={styleId} operations={analysis.operations} />
        </section>
      )}
    </div>
  )
}

function IEWorkspace() {
  const factory = useFactory()
  const lines = useLines(factory.id)
  const styles = useStyles(factory.id)
  const [lineId, setLineId] = useState('')
  const [styleId, setStyleId] = useState('')

  return (
    <div>
      <LineStyleSelectors
        lineId={lineId}
        styleId={styleId}
        onLineChange={setLineId}
        onStyleChange={setStyleId}
        lines={lines.data ?? []}
        styles={styles.data ?? []}
      />
      {lineId && styleId ? (
        <Analysis factoryId={factory.id} lineId={lineId} styleId={styleId} />
      ) : (
        <EmptyState icon="calculator" title="Choose a line and a style" description="Line balance and cycle-time data appear once both are selected." />
      )}
    </div>
  )
}

export function IEPage() {
  const can = useCan()
  return (
    <>
      <PageHeader
        title="Industrial engineering"
        description="Line balance, bottleneck analysis and cycle-time observations."
      />
      {can('ie:read') ? <IEWorkspace /> : <PermissionDenied message="Your role does not include access to industrial engineering data." />}
    </>
  )
}
