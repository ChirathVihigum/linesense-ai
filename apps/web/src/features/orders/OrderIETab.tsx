import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { SourceLabel } from '../../components/SourceLabel'
import { StateBadge } from '../../components/StateBadge'
import type { Schemas } from '../../lib/api'
import { formatDecimal } from '../../lib/format'
import { parseOrderReport } from './orderReport'

type Operation = Schemas['OperationOut']

const COLUMNS: Column<Operation>[] = [
  { key: 'sequence', header: 'Seq', align: 'right', render: (row) => String(row.sequence) },
  { key: 'code', header: 'Code', render: (row) => <span className="font-mono text-xs">{row.code}</span> },
  { key: 'name', header: 'Operation', render: (row) => row.name },
  { key: 'sam_minutes', header: 'SAM (minutes)', align: 'right', render: (row) => formatDecimal(row.sam_minutes) },
]

/** The style's standard operations and the run's IE assessment, when one exists. */
export function OrderIETab({ order }: { order: Schemas['OrderDetail'] }) {
  const report = parseOrderReport(order.latest_report)
  const ieSummary = report?.agentSummaries.find((summary) => summary.agent === 'ie') ?? null

  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="mb-2 text-base font-semibold">Standard operations</h2>
        {order.operations.length === 0 ? (
          <EmptyState icon="info" title="No operations" description="This style has no defined operations." />
        ) : (
          <DataTable caption="Operations" columns={COLUMNS} rows={order.operations} rowKey={(row) => row.code} />
        )}
      </section>
      <section>
        <h2 className="mb-2 text-base font-semibold">Line capability assessment</h2>
        {ieSummary ? (
          <div className="panel flex flex-col gap-1.5 p-3">
            <StateBadge vocabulary="agent_result" state={ieSummary.status} />
            <p>{ieSummary.summary}</p>
            <SourceLabel source={ieSummary.summarySource === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }} />
          </div>
        ) : (
          <EmptyState icon="info" title="No IE assessment yet" description="Run an analysis to get a line capability assessment." />
        )}
      </section>
    </div>
  )
}
