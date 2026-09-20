import { StateBadge } from '../../components/StateBadge'
import { SourceLabel } from '../../components/SourceLabel'
import { Icon } from '../../components/Icon'
import type { Schemas } from '../../lib/api'
import { formatDateTime, formatInteger, humanizeCode } from '../../lib/format'
import { EvidenceList } from '../evidence/EvidenceList'
import { fromEvidenceRef } from '../evidence/evidenceItem'
import { FindingList } from './FindingList'
import { MetricTable } from './MetricTable'

type AgentResult = Schemas['AgentResult']

const AGENT_LABEL: Record<AgentResult['agent'], string> = {
  planning: 'Planning',
  rm: 'Raw materials',
  ie: 'Industrial engineering',
  quality: 'Quality',
}

/** One agent's full result within a run: status, findings, metrics, recommended
 * actions, data quality and execution metadata. */
export function AgentResultCard({
  result,
  factoryId,
  timeZone,
}: {
  result: AgentResult
  factoryId: string
  timeZone: string
}) {
  const metadata = result.execution_metadata
  return (
    <section className="panel flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold">{AGENT_LABEL[result.agent]}</h3>
        <StateBadge vocabulary="agent_result" state={result.status} />
      </div>

      <div className="flex flex-col gap-1.5">
        <p>{result.summary}</p>
        <SourceLabel
          source={result.summary_source === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }}
        />
      </div>

      {result.warnings.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-md border border-warn-line bg-warn-bg p-2.5 text-warn-fg">
          {result.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-1.5 text-sm">
              <Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {warning}
            </li>
          ))}
        </ul>
      )}

      <div>
        <h4 className="mb-1.5 text-xs font-medium text-fg-muted">Findings</h4>
        <FindingList findings={result.findings} />
      </div>

      <div>
        <h4 className="mb-1.5 text-xs font-medium text-fg-muted">Metrics</h4>
        <MetricTable metrics={result.metrics} />
      </div>

      {result.recommended_actions.length > 0 && (
        <div>
          <h4 className="mb-1.5 text-xs font-medium text-fg-muted">Recommended actions</h4>
          <ul className="flex flex-col gap-1.5">
            {result.recommended_actions
              .slice()
              .sort((a, b) => a.rank - b.rank)
              .map((action) => (
                <li key={action.action_id} className="panel flex flex-col gap-1 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-fg-muted">
                      #{action.rank} · {humanizeCode(action.kind)}
                    </span>
                    <SourceLabel
                      source={action.source === 'model' ? { kind: 'ai_recommendation' } : { kind: 'calculated' }}
                    />
                  </div>
                  <p className="text-sm">{action.summary}</p>
                </li>
              ))}
          </ul>
        </div>
      )}

      {!result.data_quality.complete && (
        <div className="rounded-md border border-warn-line bg-warn-bg p-2.5 text-warn-fg">
          <p className="text-sm font-semibold">Data quality</p>
          {result.data_quality.missing.length > 0 && (
            <p className="text-sm">Missing: {result.data_quality.missing.join(', ')}</p>
          )}
          {result.data_quality.notes.map((note) => (
            <p key={note} className="text-sm">
              {note}
            </p>
          ))}
        </div>
      )}

      <div>
        <h4 className="mb-1.5 text-xs font-medium text-fg-muted">Evidence</h4>
        <EvidenceList
          evidence={result.evidence_refs.map((ref) => fromEvidenceRef(result.agent, ref))}
          factoryId={factoryId}
        />
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-fg-muted sm:grid-cols-3">
        <dt>Provider</dt>
        <dd className="text-fg">{metadata.provider}</dd>
        <dt>Model</dt>
        <dd className="text-fg">{metadata.model}</dd>
        <dt>Model calls</dt>
        <dd className="text-fg tabular-nums">{formatInteger(metadata.model_calls)}</dd>
        <dt>Tokens</dt>
        <dd className="text-fg tabular-nums">
          {formatInteger(metadata.input_tokens)} in / {formatInteger(metadata.output_tokens)} out
        </dd>
        <dt>Prompt version</dt>
        <dd className="text-fg font-mono">{metadata.prompt_version}</dd>
        <dt>Tool calls</dt>
        <dd className="text-fg">{metadata.tool_calls.length > 0 ? metadata.tool_calls.join(', ') : 'None'}</dd>
        <dt>Started</dt>
        <dd className="text-fg">{formatDateTime(metadata.started_at, timeZone)}</dd>
        <dt>Completed</dt>
        <dd className="text-fg">{formatDateTime(metadata.completed_at, timeZone)}</dd>
        {metadata.degraded && (
          <>
            <dt>Degraded reason</dt>
            <dd className="text-fg">{metadata.degraded_reason ?? 'Unknown'}</dd>
          </>
        )}
      </dl>
    </section>
  )
}
