import { Link } from 'react-router'

import { DegradedBanner } from '../../components/DegradedBanner'
import { EmptyState } from '../../components/EmptyState'
import { Icon } from '../../components/Icon'
import { ShipmentEligibilityBadge } from '../../components/ShipmentEligibilityBadge'
import { SourceLabel } from '../../components/SourceLabel'
import { StaleBanner } from '../../components/StaleBanner'
import { StateBadge } from '../../components/StateBadge'
import type { Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { humanizeCode } from '../../lib/format'
import { EvidenceList } from '../evidence/EvidenceList'
import { fromReportEvidence } from '../evidence/evidenceItem'
import { parseOrderReport, type ReportBlocker } from './orderReport'

const AGENT_LABEL: Record<string, string> = {
  planning: 'Planning',
  rm: 'Raw materials',
  ie: 'Industrial engineering',
  quality: 'Quality',
}

const SEVERITY_ICON: Record<ReportBlocker['severity'], 'ban' | 'alert' | 'info'> = {
  critical: 'ban',
  warning: 'alert',
  info: 'info',
}

export function OrderOverviewTab({ order }: { order: Schemas['OrderDetail'] }) {
  const factory = useFactory()
  const report = parseOrderReport(order.latest_report)

  if (!report) {
    return (
      <EmptyState
        icon="info"
        title="No analysis yet"
        description="Start an analysis to see a grounded status summary here."
      />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {report.stale && <StaleBanner message="The order has changed since this analysis ran." />}
      {report.degraded && (
        <DegradedBanner message={report.degradedReasons.join(', ') || 'Some agents did not complete normally.'} />
      )}

      <section>
        <h2 className="mb-2 text-base font-semibold">Shipment eligibility</h2>
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <ShipmentEligibilityBadge
              eligible={report.shipment.eligible}
              eligibleLabel="Eligible"
              ineligibleLabel="Not eligible"
            />
            <SourceLabel source={{ kind: 'calculated' }} />
          </div>
          {report.shipment.reasons.length > 0 && (
            <ul className="text-sm text-fg-muted">
              {report.shipment.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">Blockers</h2>
        {report.blockers.length === 0 ? (
          <p className="text-fg-muted">No blockers.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {report.blockers.map((blocker) => (
              <li key={`${blocker.code}-${blocker.agent}`} className="panel flex items-start gap-2 p-2.5">
                <Icon name={SEVERITY_ICON[blocker.severity]} className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div>
                  <p className="text-sm">{blocker.message}</p>
                  <p className="text-xs text-fg-muted">
                    {AGENT_LABEL[blocker.agent] ?? blocker.agent} · {blocker.code}
                  </p>
                </div>
                <SourceLabel
                  source={blocker.source === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">Agent summaries</h2>
        <ul className="flex flex-col gap-1.5">
          {report.agentSummaries.map((summary) => (
            <li key={summary.agent} className="panel flex flex-col gap-1 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{AGENT_LABEL[summary.agent] ?? humanizeCode(summary.agent)}</span>
                <StateBadge vocabulary="agent_result" state={summary.status} />
              </div>
              <p className="text-sm">{summary.summary}</p>
              <SourceLabel source={summary.summarySource === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }} />
              {summary.degradedReason && <p className="text-xs text-warn-fg">Degraded: {summary.degradedReason}</p>}
            </li>
          ))}
        </ul>
      </section>

      {report.recommendation && (
        <section>
          <h2 className="mb-2 text-base font-semibold">Recommendation</h2>
          <Link
            className="link"
            to={`/f/${encodeURIComponent(factory.code)}/approvals/${report.recommendation.id}`}
          >
            Review {humanizeCode(report.recommendation.kind)} recommendation ({humanizeCode(report.recommendation.status)})
          </Link>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-base font-semibold">Evidence</h2>
        <EvidenceList evidence={report.evidence.map(fromReportEvidence)} factoryId={factory.id} />
      </section>
    </div>
  )
}
