import type { ReportEvidenceItem } from '../orders/orderReport'
import type { Schemas } from '../../lib/api'

/**
 * One evidence reference, normalized from whichever shape it arrived in
 * (a report's raw `EvidenceRef`, an agent result's `evidence_refs`, or a
 * recommendation's already-resolved `EvidenceOut`) so `EvidenceList` and
 * `CitationDrawer` render all three the same way.
 */
export interface EvidenceItem {
  evidenceId: string
  agent: string | null
  kind: string
  description: string
  recordType: string | null
  recordId: string | null
  recordVersion: number | null
  documentId: string | null
  documentTitle: string | null
  documentVersionNo: number | null
  pageNumber: number | null
  section: string | null
  chunkId: string | null
}

export function fromReportEvidence(item: ReportEvidenceItem): EvidenceItem {
  return {
    evidenceId: item.evidenceId,
    agent: item.agent,
    kind: item.kind,
    description: item.description,
    recordType: item.recordType,
    recordId: item.recordId,
    recordVersion: item.recordVersion,
    documentId: item.documentId,
    documentTitle: null,
    documentVersionNo: null,
    pageNumber: item.pageNumber,
    section: item.section,
    chunkId: item.chunkId,
  }
}

export function fromEvidenceRef(agent: string, ref: Schemas['EvidenceRef']): EvidenceItem {
  return {
    evidenceId: ref.evidence_id,
    agent,
    kind: ref.kind,
    description: ref.description,
    recordType: ref.record_type ?? null,
    recordId: ref.record_id ?? null,
    recordVersion: ref.record_version ?? null,
    documentId: ref.document_id ?? null,
    documentTitle: null,
    documentVersionNo: null,
    pageNumber: ref.page_number ?? null,
    section: ref.section ?? null,
    chunkId: ref.chunk_id ?? null,
  }
}

export function fromEvidenceOut(evidence: Schemas['EvidenceOut']): EvidenceItem {
  return {
    evidenceId: evidence.evidence_id,
    agent: evidence.agent,
    kind: evidence.kind,
    description: evidence.description,
    recordType: evidence.record_type,
    recordId: evidence.record_id,
    recordVersion: evidence.record_version,
    documentId: evidence.document_id,
    documentTitle: evidence.document_title,
    documentVersionNo: evidence.document_version_no,
    pageNumber: evidence.page_number,
    section: evidence.section,
    chunkId: evidence.chunk_id,
  }
}
