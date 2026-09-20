import { humanizeCode } from '../lib/format'
import type { IconName } from './Icon'

export type StateVocabulary =
  | 'production'
  | 'material'
  | 'quality'
  | 'analysis'
  | 'agent_result'
  | 'recommendation'
  | 'reservation'
  | 'policy'
  | 'document_version'
  | 'audit_outcome'

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'muted'

export interface StateStyle {
  label: string
  icon: IconName
  tone: Tone
}

/** State vocabularies from backend-contracts.md §3. Every state has its own icon + text. */
export const STATE_STYLES: Record<StateVocabulary, Record<string, StateStyle>> = {
  production: {
    DRAFT: { label: 'Draft', icon: 'pencil', tone: 'neutral' },
    VALIDATED: { label: 'Validated', icon: 'check-circle', tone: 'info' },
    PLANNED: { label: 'Planned', icon: 'calendar', tone: 'info' },
    IN_PRODUCTION: { label: 'In production', icon: 'play', tone: 'info' },
    PRODUCTION_COMPLETE: { label: 'Production complete', icon: 'flag', tone: 'success' },
    DISPATCHED: { label: 'Dispatched', icon: 'truck', tone: 'success' },
    CANCELLED: { label: 'Cancelled', icon: 'x-circle', tone: 'muted' },
  },
  material: {
    UNKNOWN: { label: 'Unknown', icon: 'question', tone: 'neutral' },
    READY: { label: 'Ready', icon: 'check', tone: 'success' },
    AT_RISK: { label: 'At risk', icon: 'alert', tone: 'warning' },
    SHORTAGE: { label: 'Shortage', icon: 'ban', tone: 'danger' },
  },
  quality: {
    NOT_INSPECTED: { label: 'Not inspected', icon: 'circle-dashed', tone: 'neutral' },
    PENDING: { label: 'Pending', icon: 'clock', tone: 'info' },
    HOLD: { label: 'On hold', icon: 'lock', tone: 'danger' },
    RELEASED: { label: 'Released', icon: 'unlock', tone: 'success' },
  },
  analysis: {
    QUEUED: { label: 'Queued', icon: 'clock', tone: 'neutral' },
    RUNNING: { label: 'Running', icon: 'refresh', tone: 'info' },
    AWAITING_REVIEW: { label: 'Awaiting review', icon: 'eye', tone: 'warning' },
    COMPLETED: { label: 'Completed', icon: 'check-circle', tone: 'success' },
    DEGRADED: { label: 'Degraded', icon: 'alert', tone: 'warning' },
    FAILED: { label: 'Failed', icon: 'x-circle', tone: 'danger' },
    CANCELLED: { label: 'Cancelled', icon: 'ban', tone: 'muted' },
  },
  agent_result: {
    SUCCEEDED: { label: 'Succeeded', icon: 'check-circle', tone: 'success' },
    DEGRADED: { label: 'Degraded', icon: 'alert', tone: 'warning' },
    FAILED: { label: 'Failed', icon: 'x-circle', tone: 'danger' },
  },
  recommendation: {
    DRAFT: { label: 'Draft', icon: 'pencil', tone: 'neutral' },
    PROPOSED: { label: 'Proposed', icon: 'send', tone: 'info' },
    APPROVED: { label: 'Approved', icon: 'check-circle', tone: 'success' },
    REJECTED: { label: 'Rejected', icon: 'x-circle', tone: 'danger' },
    EXPIRED: { label: 'Expired', icon: 'hourglass', tone: 'muted' },
    APPLIED: { label: 'Applied', icon: 'check-double', tone: 'success' },
    SUPERSEDED: { label: 'Superseded', icon: 'layers', tone: 'muted' },
  },
  reservation: {
    ACTIVE: { label: 'Active', icon: 'clock', tone: 'info' },
    RELEASED: { label: 'Released', icon: 'unlock', tone: 'success' },
    CONSUMED: { label: 'Consumed', icon: 'check-double', tone: 'muted' },
  },
  policy: {
    DRAFT: { label: 'Draft', icon: 'pencil', tone: 'neutral' },
    ACTIVE: { label: 'Active', icon: 'check-circle', tone: 'success' },
    RETIRED: { label: 'Retired', icon: 'ban', tone: 'muted' },
  },
  document_version: {
    QUARANTINE: { label: 'Quarantine', icon: 'lock', tone: 'warning' },
    PROCESSING: { label: 'Processing', icon: 'refresh', tone: 'info' },
    ACTIVE: { label: 'Active', icon: 'check-circle', tone: 'success' },
    REJECTED: { label: 'Rejected', icon: 'x-circle', tone: 'danger' },
    SUPERSEDED: { label: 'Superseded', icon: 'layers', tone: 'muted' },
  },
  audit_outcome: {
    SUCCESS: { label: 'Success', icon: 'check-circle', tone: 'success' },
    DENIED: { label: 'Denied', icon: 'lock', tone: 'danger' },
    FAILED: { label: 'Failed', icon: 'x-circle', tone: 'danger' },
  },
}

export const VOCABULARY_LABELS: Record<StateVocabulary, string> = {
  production: 'Production',
  material: 'Materials',
  quality: 'Quality',
  analysis: 'Analysis',
  agent_result: 'Agent result',
  recommendation: 'Recommendation',
  reservation: 'Reservation',
  policy: 'Quality policy',
  document_version: 'Document version',
  audit_outcome: 'Outcome',
}

/** Status tones map to semantic tokens (DESIGN.md); `muted` is for terminal/inactive states. */
export const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'border-neutral-line bg-neutral-bg text-neutral-fg',
  info: 'border-info-line bg-info-bg text-info-fg',
  success: 'border-ok-line bg-ok-bg text-ok-fg',
  warning: 'border-warn-line bg-warn-bg text-warn-fg',
  danger: 'border-bad-line bg-bad-bg text-bad-fg',
  muted: 'border-dashed border-neutral-line bg-transparent text-fg-muted',
}

export function stateStyle(vocabulary: StateVocabulary, state: string): StateStyle {
  return (
    STATE_STYLES[vocabulary][state] ?? { label: humanizeCode(state), icon: 'question', tone: 'neutral' }
  )
}
