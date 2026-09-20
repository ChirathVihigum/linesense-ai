import { useQuery } from '@tanstack/react-query'

import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { StateBadge } from '../../components/StateBadge'
import { api, unwrap, type Schemas } from '../../lib/api'
import { formatDateTime } from '../../lib/format'

type DocumentVersionOut = Schemas['DocumentVersionOut']

const IN_FLIGHT_STATUSES = new Set(['QUARANTINE', 'PROCESSING'])
const POLL_INTERVAL_MS = 3_000

/**
 * A document's version history, newest first. Polls while any version is
 * still being processed (`QUARANTINE`/`PROCESSING`), so an upload's status
 * updates without a manual refresh.
 */
export function DocumentVersions({
  documentId,
  documentLabel,
  timeZone,
  onClose,
}: {
  documentId: string
  documentLabel: string
  timeZone: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['document-versions', documentId],
    queryFn: () => unwrap(api.GET('/api/v1/documents/{document_id}', { params: { path: { document_id: documentId } } })),
    refetchInterval: (q) => {
      const versions = q.state.data?.versions ?? []
      return versions.some((version) => IN_FLIGHT_STATUSES.has(version.status)) ? POLL_INTERVAL_MS : false
    },
  })

  return (
    <section aria-label={`Versions of ${documentLabel}`} className="panel flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Versions: {documentLabel}</h2>
        <button type="button" className="btn-secondary h-8" onClick={onClose}>
          <Icon name="x" />
          Close
        </button>
      </div>
      {query.isPending && <LoadingState label="Loading versions…" variant="block" rows={3} />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.isSuccess && (
        <ul className="flex flex-col gap-3">
          {query.data.versions.map((version: DocumentVersionOut) => (
            <li key={version.id} className="flex flex-col gap-1 border-b border-line pb-3 last:border-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs font-medium">v{version.version_no}</span>
                <StateBadge vocabulary="document_version" state={version.status} />
                <span className="text-xs text-fg-muted">{version.media_type}</span>
                <span className="text-xs text-fg-muted">{Math.round(version.size_bytes / 1024)} KB</span>
              </div>
              {version.status === 'REJECTED' && version.rejection_reason && (
                <p className="text-xs text-bad-fg">{version.rejection_reason}</p>
              )}
              <p className="text-xs text-fg-muted">
                Uploaded {formatDateTime(version.created_at, timeZone)}
                {version.activated_at && <> · Activated {formatDateTime(version.activated_at, timeZone)}</>}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
