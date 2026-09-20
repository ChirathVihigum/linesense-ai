/**
 * The order's latest canonical report (`OrderDetail.latest_report`), built by
 * `app/orchestration/synthesis.py::build_order_report` and served untyped
 * (`dict[str, Any] | null`) because it predates this contract's OpenAPI
 * schema. This module mirrors that Pydantic shape and parses it defensively:
 * a report that does not match (a field renamed, a future shape) renders as
 * "no report" rather than crashing the order detail page or showing
 * fabricated data.
 */

export interface ReportBlocker {
  code: string
  message: string
  agent: string
  severity: 'info' | 'warning' | 'critical'
  evidenceIds: string[]
  source: 'deterministic' | 'model'
}

export interface ReportAgentSummary {
  agent: string
  status: string
  summary: string
  summarySource: 'deterministic' | 'model'
  provider: string
  model: string
  degradedReason: string | null
  findingCodes: string[]
}

export interface ReportRecommendation {
  id: string
  status: string
  kind: string
  sourceLabel: string
}

export interface ReportEvidenceItem {
  agent: string
  evidenceId: string
  kind: string
  description: string
  recordType: string | null
  recordId: string | null
  recordVersion: number | null
  documentId: string | null
  documentVersionId: string | null
  chunkId: string | null
  pageNumber: number | null
  section: string | null
}

export interface OrderReportData {
  states: { production: string; material: string; quality: string; analysis: string }
  shipment: { eligible: boolean; reasons: string[]; source: string }
  blockers: ReportBlocker[]
  agentSummaries: ReportAgentSummary[]
  recommendation: ReportRecommendation | null
  evidence: ReportEvidenceItem[]
  degraded: boolean
  degradedReasons: string[]
  generatedAt: string
  /** True when the order changed since the run this report was reasoned about. */
  stale: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function parseBlocker(value: unknown): ReportBlocker | null {
  if (!isRecord(value)) return null
  if (typeof value.code !== 'string' || typeof value.message !== 'string') return null
  return {
    code: value.code,
    message: value.message,
    agent: typeof value.agent === 'string' ? value.agent : 'unknown',
    severity: value.severity === 'info' || value.severity === 'warning' ? value.severity : 'critical',
    evidenceIds: asStringArray(value.evidence_ids),
    source: value.source === 'model' ? 'model' : 'deterministic',
  }
}

function parseAgentSummary(value: unknown): ReportAgentSummary | null {
  if (!isRecord(value)) return null
  if (typeof value.agent !== 'string' || typeof value.status !== 'string') return null
  return {
    agent: value.agent,
    status: value.status,
    summary: typeof value.summary === 'string' ? value.summary : '',
    summarySource: value.summary_source === 'model' ? 'model' : 'deterministic',
    provider: typeof value.provider === 'string' ? value.provider : 'unknown',
    model: typeof value.model === 'string' ? value.model : 'unknown',
    degradedReason: typeof value.degraded_reason === 'string' ? value.degraded_reason : null,
    findingCodes: asStringArray(value.finding_codes),
  }
}

function parseRecommendation(value: unknown): ReportRecommendation | null {
  if (!isRecord(value)) return null
  if (typeof value.id !== 'string' || typeof value.status !== 'string') return null
  return {
    id: value.id,
    status: value.status,
    kind: typeof value.kind === 'string' ? value.kind : 'unknown',
    sourceLabel: typeof value.source_label === 'string' ? value.source_label : '',
  }
}

function parseEvidenceItem(value: unknown): ReportEvidenceItem | null {
  if (!isRecord(value)) return null
  if (typeof value.evidence_id !== 'string') return null
  return {
    agent: typeof value.agent === 'string' ? value.agent : 'unknown',
    evidenceId: value.evidence_id,
    kind: typeof value.kind === 'string' ? value.kind : 'record',
    description: typeof value.description === 'string' ? value.description : '',
    recordType: typeof value.record_type === 'string' ? value.record_type : null,
    recordId: typeof value.record_id === 'string' ? value.record_id : null,
    recordVersion: typeof value.record_version === 'number' ? value.record_version : null,
    documentId: typeof value.document_id === 'string' ? value.document_id : null,
    documentVersionId: typeof value.document_version_id === 'string' ? value.document_version_id : null,
    chunkId: typeof value.chunk_id === 'string' ? value.chunk_id : null,
    pageNumber: typeof value.page_number === 'number' ? value.page_number : null,
    section: typeof value.section === 'string' ? value.section : null,
  }
}

/** Parses `OrderDetail.latest_report`; returns `null` for a missing or malformed report. */
export function parseOrderReport(value: unknown): OrderReportData | null {
  if (!isRecord(value)) return null
  const states = value.states
  const shipment = value.shipment
  if (!isRecord(states) || !isRecord(shipment)) return null
  if (
    typeof states.production !== 'string' ||
    typeof states.material !== 'string' ||
    typeof states.quality !== 'string' ||
    typeof states.analysis !== 'string'
  ) {
    return null
  }
  const blockers = Array.isArray(value.blockers)
    ? value.blockers.map(parseBlocker).filter((b): b is ReportBlocker => b !== null)
    : []
  const agentSummaries = Array.isArray(value.agent_summaries)
    ? value.agent_summaries.map(parseAgentSummary).filter((s): s is ReportAgentSummary => s !== null)
    : []
  const evidence = Array.isArray(value.evidence)
    ? value.evidence.map(parseEvidenceItem).filter((e): e is ReportEvidenceItem => e !== null)
    : []
  return {
    states: {
      production: states.production,
      material: states.material,
      quality: states.quality,
      analysis: states.analysis,
    },
    shipment: {
      eligible: shipment.eligible === true,
      reasons: asStringArray(shipment.reasons),
      source: typeof shipment.source === 'string' ? shipment.source : 'Calculated from records',
    },
    blockers,
    agentSummaries,
    recommendation: parseRecommendation(value.recommendation),
    evidence,
    degraded: value.degraded === true,
    degradedReasons: asStringArray(value.degraded_reasons),
    generatedAt: typeof value.generated_at === 'string' ? value.generated_at : '',
    stale: value.stale === true,
  }
}
