import { useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { LoadingState } from '../../components/LoadingState'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'

type SearchResultOut = Schemas['SearchResultOut']
type CitationOut = Schemas['CitationOut']
type SearchMode = 'hybrid' | 'lexical' | 'vector'

const MODES: { value: SearchMode; label: string }[] = [
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'lexical', label: 'Lexical' },
  { value: 'vector', label: 'Vector' },
]

function CitationPanel({ chunkId, onClose }: { chunkId: string; onClose: () => void }) {
  const factory = useFactory()
  const query = useQuery({
    queryKey: ['citation', factory.id, chunkId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/citations/{chunk_id}', {
          params: { path: { chunk_id: chunkId }, query: { factory_id: factory.id } },
        }),
      ),
  })

  return (
    <div className="panel flex flex-col gap-3 p-4" role="region" aria-label="Citation">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Citation</h3>
        <button type="button" className="btn-secondary h-8" onClick={onClose}>
          <Icon name="x" />
          Close
        </button>
      </div>
      {query.isPending && <LoadingState label="Loading citation…" variant="block" rows={2} />}
      {query.isError && <ErrorState error={query.error} onRetry={() => void query.refetch()} />}
      {query.isSuccess && <CitationBody citation={query.data} />}
    </div>
  )
}

function CitationBody({ citation }: { citation: CitationOut }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <p className="font-medium">
        {citation.document.title} · v{citation.version_no}
      </p>
      <p className="text-xs text-fg-muted">
        {citation.section ?? 'No section'}
        {citation.page_number !== null && <> · Page {citation.page_number}</>}
      </p>
      <p className="mt-1 whitespace-pre-wrap">{citation.text}</p>
    </div>
  )
}

function ResultRow({ result, onOpenCitation }: { result: SearchResultOut; onOpenCitation: (chunkId: string) => void }) {
  return (
    <li className="flex flex-col gap-1 border-b border-line py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">
          {result.title} · v{result.version_no}
        </span>
        <span className="tabular-nums text-xs text-fg-muted">Score {result.score.toFixed(3)}</span>
      </div>
      <p className="text-xs text-fg-muted">
        {result.section ?? 'No section'}
        {result.page_number !== null && <> · Page {result.page_number}</>}
      </p>
      <p className="whitespace-pre-wrap">{result.text}</p>
      <div>
        <button
          type="button"
          className="link text-xs"
          onClick={() => {
            onOpenCitation(result.chunk_id)
          }}
        >
          Open citation
        </button>
      </div>
    </li>
  )
}

export function SearchPanel() {
  const factory = useFactory()
  const inputId = useId()
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [submitted, setSubmitted] = useState<{ q: string; mode: SearchMode } | null>(null)
  const [openChunkId, setOpenChunkId] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['search', factory.id, submitted],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/factories/{factory_id}/search', {
          params: {
            path: { factory_id: factory.id },
            query: { q: submitted?.q ?? '', mode: submitted?.mode ?? 'hybrid' },
          },
        }),
      ),
    enabled: submitted !== null,
  })

  return (
    <div className="flex flex-col gap-4">
      <form
        role="search"
        className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_auto_auto]"
        onSubmit={(event) => {
          event.preventDefault()
          if (q.trim().length === 0) return
          setSubmitted({ q, mode })
        }}
      >
        <FormField id={inputId} label="Search the knowledge base">
          <input
            id={inputId}
            className="input"
            value={q}
            onChange={(event) => {
              setQ(event.target.value)
            }}
          />
        </FormField>
        <FormField id={`${inputId}-mode`} label="Mode">
          <select
            id={`${inputId}-mode`}
            className="input"
            value={mode}
            onChange={(event) => {
              setMode(event.target.value as SearchMode)
            }}
          >
            {MODES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </FormField>
        <div className="flex items-end">
          <button type="submit" className="btn-primary h-9" disabled={q.trim().length === 0}>
            <Icon name="search" />
            Search
          </button>
        </div>
      </form>

      {query.isPending && submitted && <LoadingState label="Searching…" variant="block" rows={3} />}
      {query.isError &&
        (query.error instanceof ApiError && query.error.status === 403 ? (
          <ErrorState error={query.error} title="Search is not available" />
        ) : (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ))}
      {query.isSuccess && query.data.items.length === 0 && (
        <EmptyState icon="search" title="No matches" description="Try a different search term or mode." />
      )}
      {query.isSuccess && query.data.items.length > 0 && (
        <ul className="panel divide-y divide-line px-4">
          {query.data.items.map((result) => (
            <ResultRow key={result.chunk_id} result={result} onOpenCitation={setOpenChunkId} />
          ))}
        </ul>
      )}
      {openChunkId && (
        <CitationPanel
          chunkId={openChunkId}
          onClose={() => {
            setOpenChunkId(null)
          }}
        />
      )}
    </div>
  )
}
