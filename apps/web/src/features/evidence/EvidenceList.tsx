import { useState } from 'react'

import { EmptyState } from '../../components/EmptyState'
import { Icon } from '../../components/Icon'
import { formatInteger } from '../../lib/format'
import { CitationDrawer } from './CitationDrawer'
import type { EvidenceItem } from './evidenceItem'

const KIND_ICON: Record<string, 'calculator' | 'file-csv' | 'info'> = {
  record: 'calculator',
  document: 'file-csv',
  calculation: 'calculator',
}

function recordRefText(item: EvidenceItem): string | null {
  if (item.kind !== 'record' || !item.recordType) return null
  const shortId = item.recordId ? item.recordId.slice(0, 8) : 'unknown'
  const version = item.recordVersion !== null ? ` (v${formatInteger(item.recordVersion)})` : ''
  return `${item.recordType} ${shortId}${version}`
}

function documentRefText(item: EvidenceItem): string | null {
  if (item.kind !== 'document') return null
  const title = item.documentTitle ?? 'Document reference'
  const page = item.pageNumber !== null ? `, page ${formatInteger(item.pageNumber)}` : ''
  const section = item.section ? `, ${item.section}` : ''
  return `${title}${page}${section}`
}

/** All evidence for an agent result, report or recommendation, with record refs shown as plain
 * text and document refs opening `CitationDrawer` for the cited chunk's text. */
export function EvidenceList({ evidence, factoryId }: { evidence: EvidenceItem[]; factoryId: string }) {
  const [openChunkId, setOpenChunkId] = useState<string | null>(null)

  if (evidence.length === 0) {
    return <EmptyState icon="info" title="No evidence" description="This result cited no records or documents." />
  }

  return (
    <ul aria-label="Evidence" className="flex flex-col gap-2">
      {evidence.map((item) => (
        <li key={item.evidenceId} className="panel flex flex-col gap-1 p-3">
          <div className="flex items-start gap-2">
            <Icon name={KIND_ICON[item.kind] ?? 'info'} className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-muted" />
            <div className="flex flex-col gap-1">
              <p>{item.description}</p>
              <p className="text-xs text-fg-muted">
                {item.agent && <span className="font-medium">{item.agent}</span>}
                {recordRefText(item) && <span> · {recordRefText(item)}</span>}
                {documentRefText(item) && <span> · {documentRefText(item)}</span>}
              </p>
            </div>
          </div>
          {item.kind === 'document' && item.chunkId && (
            <button
              type="button"
              className="btn-secondary h-8 w-fit"
              onClick={() => {
                setOpenChunkId(item.chunkId)
              }}
            >
              <Icon name="eye" />
              View source
            </button>
          )}
        </li>
      ))}
      {openChunkId && (
        <CitationDrawer
          chunkId={openChunkId}
          factoryId={factoryId}
          onClose={() => {
            setOpenChunkId(null)
          }}
        />
      )}
    </ul>
  )
}
