import { EmptyState } from '../../components/EmptyState'
import type { Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { EvidenceList } from '../evidence/EvidenceList'
import { fromReportEvidence } from '../evidence/evidenceItem'
import { parseOrderReport } from './orderReport'

/** All evidence the latest analysis cited: record references (plain text) and
 * document references (open `CitationDrawer` for the cited chunk's text). */
export function OrderEvidenceTab({ order }: { order: Schemas['OrderDetail'] }) {
  const factory = useFactory()
  const report = parseOrderReport(order.latest_report)

  if (!report || report.evidence.length === 0) {
    return (
      <EmptyState
        icon="info"
        title="No evidence yet"
        description="Run an analysis to see the records and documents it cites."
      />
    )
  }
  return <EvidenceList evidence={report.evidence.map(fromReportEvidence)} factoryId={factory.id} />
}
