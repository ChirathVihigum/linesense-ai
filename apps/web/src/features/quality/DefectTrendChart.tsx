import { useQuery } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { formatInteger, formatPercent } from '../../lib/format'

type DefectCodeTrend = Schemas['DefectCodeTrendOut']

const WINDOW_DAYS = 30

/** Defects by code over the last 30 days, with the numbers in a table beside the chart. */
export function DefectTrendChart() {
  const factory = useFactory()
  const query = useQuery({
    queryKey: ['quality-trends', factory.id, WINDOW_DAYS],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/quality/trends', {
          params: { path: { factory_id: factory.id }, query: { days: WINDOW_DAYS } },
        }),
      ),
  })

  if (query.isPending) return <LoadingState label="Loading defect trends…" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  const trend = query.data
  if (trend.by_defect_code.length === 0) {
    return <EmptyState icon="check-circle" title="No defects recorded" description={`No defects were recorded in the last ${WINDOW_DAYS} days.`} />
  }

  const summary = `${formatInteger(trend.defective_units)} defective of ${formatInteger(trend.inspected_units)} inspected units (${formatPercent(trend.defective_rate !== null ? Number(trend.defective_rate) : null, 1)}) over ${WINDOW_DAYS} days`

  const columns: Column<DefectCodeTrend>[] = [
    { key: 'defect_code', header: 'Defect code', render: (row) => <span className="font-mono text-xs">{row.defect_code}</span> },
    { key: 'severity', header: 'Severity', render: (row) => row.severity },
    { key: 'count', header: 'Count', align: 'right', render: (row) => formatInteger(row.count) },
  ]

  return (
    <div className="flex flex-col gap-3">
      <figure aria-label={summary} className="flex flex-col gap-2">
        <div className="h-64 w-full" aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={trend.by_defect_code} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-line" />
              <XAxis dataKey="defect_code" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} className="fill-accent" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <figcaption className="text-sm font-medium">{summary}</figcaption>
      </figure>
      <DataTable caption="Defects by code" columns={columns} rows={trend.by_defect_code} rowKey={(row) => row.defect_code} />
    </div>
  )
}
