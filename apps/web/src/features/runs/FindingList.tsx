import { Icon } from '../../components/Icon'
import { SourceLabel } from '../../components/SourceLabel'
import type { Schemas } from '../../lib/api'

type Finding = Schemas['Finding']

const SEVERITY_ORDER: Finding['severity'][] = ['critical', 'warning', 'info']
const SEVERITY_LABEL: Record<Finding['severity'], string> = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
}
const SEVERITY_ICON: Record<Finding['severity'], 'ban' | 'alert' | 'info'> = {
  critical: 'ban',
  warning: 'alert',
  info: 'info',
}

/** An agent result's findings, grouped by severity, each with its determinism source. */
export function FindingList({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return <p className="text-fg-muted">No findings.</p>
  }
  const bySeverity = SEVERITY_ORDER.map((severity) => ({
    severity,
    items: findings.filter((finding) => finding.severity === severity),
  })).filter((group) => group.items.length > 0)

  return (
    <div className="flex flex-col gap-3">
      {bySeverity.map((group) => (
        <div key={group.severity}>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-fg-muted">
            <Icon name={SEVERITY_ICON[group.severity]} className="h-3.5 w-3.5" />
            {SEVERITY_LABEL[group.severity]} ({group.items.length})
          </h3>
          <ul className="flex flex-col gap-1.5">
            {group.items.map((finding) => (
              <li key={finding.finding_id} className="panel flex flex-col gap-1 p-2.5">
                <p className="text-sm">{finding.message}</p>
                <div className="flex items-center gap-2 text-xs text-fg-muted">
                  <span className="font-mono">{finding.code}</span>
                  <SourceLabel
                    source={finding.source === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}
