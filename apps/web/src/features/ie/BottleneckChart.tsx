import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type BarShapeProps } from 'recharts'

import type { Schemas } from '../../lib/api'
import { formatDecimal } from '../../lib/format'

type OperationAnalysis = Schemas['OperationAnalysisOut']
type LineBalance = Schemas['LineBalanceOut']

function bottleneckSummary(
  operations: OperationAnalysis[],
  balance: LineBalance | null,
): string | null {
  if (balance === null) return null
  const bottleneck = operations[balance.bottleneck_index]
  if (!bottleneck) return null
  // Fixed to one decimal place (not `formatDecimal`, which drops trailing zeros):
  // the brief's example reads "60.0 s effective", not "60 s effective".
  const effectiveSeconds = Number(balance.bottleneck_effective_seconds).toFixed(1)
  return `Bottleneck: ${bottleneck.code} ${bottleneck.name}, ${effectiveSeconds} s effective, ≈ ${formatDecimal(balance.units_per_hour, 0)} units/hour`
}

/**
 * Effective cycle seconds per operation, the bottleneck highlighted by a
 * distinct fill and an axis label suffix (never colour alone). An `aria-label`
 * carries the same summary shown as text beside the chart, and `SampleTable`
 * is the data-table alternative.
 */
export function BottleneckChart({
  operations,
  balance,
}: {
  operations: OperationAnalysis[]
  balance: LineBalance | null
}) {
  if (operations.length === 0) return null
  const data = operations.map((operation, index) => ({
    code: operation.code,
    name: operation.name,
    effective_seconds: operation.effective_seconds === null ? 0 : Number(operation.effective_seconds),
    isBottleneck: index === balance?.bottleneck_index,
  }))
  const summary = bottleneckSummary(operations, balance)

  return (
    <figure
      aria-label={summary ?? 'Effective cycle seconds by operation'}
      className="flex flex-col gap-2"
    >
      <div className="h-72 w-full" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 24 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-line" />
            <XAxis
              dataKey="code"
              // Bottleneck bars are marked by more than colour: an axis label suffix too.
              tickFormatter={(value: string) => {
                const row = data.find((candidate) => candidate.code === value)
                return row?.isBottleneck ? `${value} (bottleneck)` : value
              }}
              tick={{ fontSize: 12 }}
              interval={0}
              angle={-20}
              textAnchor="end"
              height={50}
            />
            <YAxis tick={{ fontSize: 12 }} label={{ value: 'Seconds', angle: -90, position: 'insideLeft', fontSize: 12 }} />
            <Tooltip formatter={(value) => [`${Number(value ?? 0).toFixed(1)} s`, 'Effective cycle time']} />
            <Bar
              dataKey="effective_seconds"
              radius={[4, 4, 0, 0]}
              // `Cell` is deprecated in recharts 3; `shape` is the replacement for per-bar styling.
              shape={(props: BarShapeProps) => (
                <rect
                  x={props.x}
                  y={props.y}
                  width={props.width}
                  height={props.height}
                  rx={4}
                  ry={4}
                  className={(props.payload as { isBottleneck?: boolean } | undefined)?.isBottleneck ? 'fill-bad-fg' : 'fill-accent'}
                />
              )}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {summary && <figcaption className="text-sm font-medium">{summary}</figcaption>}
    </figure>
  )
}
