import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef } from 'react'

import { ErrorState } from '../../components/ErrorState'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { api, unwrap } from '../../lib/api'
import { formatInteger } from '../../lib/format'

/**
 * A read-only panel showing a cited document chunk's text. The text is
 * rendered as a plain React text node (never `dangerouslySetInnerHTML`), so
 * markup inside a chunk (e.g. a literal `<script>` in a source document)
 * never executes; it is shown as-is.
 */
export function CitationDrawer({
  chunkId,
  factoryId,
  onClose,
}: {
  chunkId: string
  factoryId: string
  onClose: () => void
}) {
  const titleId = useId()
  const closeRef = useRef<HTMLButtonElement>(null)

  const query = useQuery({
    queryKey: ['citation', chunkId, factoryId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/citations/{chunk_id}', {
          params: { path: { chunk_id: chunkId }, query: { factory_id: factoryId } },
        }),
      ),
  })

  useEffect(() => {
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="panel max-h-[85vh] w-full max-w-2xl overflow-y-auto p-6 shadow-lg shadow-zinc-950/10"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id={titleId} className="text-base font-semibold">
            {query.data ? query.data.document.title : 'Cited document'}
          </h2>
          <button ref={closeRef} type="button" className="btn-secondary h-8" onClick={onClose}>
            <Icon name="x" />
            Close
          </button>
        </div>
        <div className="mt-4">
          {query.isPending && <LoadingState label="Loading citation…" />}
          {query.isError && (
            <ErrorState
              title="The citation could not be loaded"
              error={query.error}
              onRetry={() => void query.refetch()}
            />
          )}
          {query.data && (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-fg-muted">
                {query.data.status === 'SUPERSEDED' && 'Superseded version. '}
                Version {formatInteger(query.data.version_no)}
                {query.data.page_number !== null && `, page ${formatInteger(query.data.page_number)}`}
                {query.data.section && `, ${query.data.section}`}
              </p>
              <pre className="max-w-full overflow-x-auto rounded-md bg-surface-sunken p-3 text-sm whitespace-pre-wrap">
                {query.data.text}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
