import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable, type Column } from '../../components/DataTable'
import { EmptyState } from '../../components/EmptyState'
import { ErrorState } from '../../components/ErrorState'
import { LoadingState } from '../../components/LoadingState'
import { PageHeader } from '../../components/PageHeader'
import { PermissionDenied } from '../../components/PermissionDenied'
import { StateBadge } from '../../components/StateBadge'
import { ApiError, api, unwrap, type Schemas } from '../../lib/api'
import { useCan, useFactory } from '../../lib/factory'
import { useToast } from '../../lib/toast'
import { DocumentVersions } from './DocumentVersions'
import { SearchPanel } from './SearchPanel'
import { UploadForm } from './UploadForm'

type DocumentSummary = Schemas['DocumentSummary']

function columns(onViewVersions: (document: DocumentSummary) => void): Column<DocumentSummary>[] {
  return [
    {
      key: 'title',
      header: 'Title',
      render: (document) => (
        <div>
          <div>{document.title}</div>
          <div className="font-mono text-xs text-fg-muted">{document.slug}</div>
        </div>
      ),
    },
    { key: 'type', header: 'Type', render: (document) => document.doc_type },
    {
      key: 'scope',
      header: 'Scope',
      render: (document) => (document.factory_id ? 'This factory' : 'Organization-wide'),
    },
    {
      key: 'status',
      header: 'Latest version',
      render: (document) =>
        document.latest_version ? (
          <div className="flex flex-col items-start gap-1">
            <StateBadge vocabulary="document_version" state={document.latest_version.status} />
            {document.latest_version.status === 'REJECTED' && document.latest_version.rejection_reason && (
              <span className="text-xs text-bad-fg">{document.latest_version.rejection_reason}</span>
            )}
          </div>
        ) : (
          'No versions'
        ),
    },
    {
      key: 'acl',
      header: 'Visible to',
      render: (document) => (document.acl_roles.length === 0 ? 'Every role' : document.acl_roles.join(', ')),
    },
    {
      key: 'actions',
      header: 'Versions',
      render: (document) => (
        <button type="button" className="link" onClick={() => { onViewVersions(document) }}>
          View versions
        </button>
      ),
    },
  ]
}

function DocumentList() {
  const factory = useFactory()
  const [selected, setSelected] = useState<DocumentSummary | null>(null)

  const query = useQuery({
    queryKey: ['documents', factory.id],
    queryFn: () =>
      unwrap(api.GET('/api/v1/factories/{factory_id}/documents', { params: { path: { factory_id: factory.id } } })),
  })

  if (query.isPending) return <LoadingState label="Loading documents…" variant="table" />
  if (query.isError) {
    return query.error instanceof ApiError && query.error.status === 403 ? (
      <PermissionDenied />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }
  if (query.data.items.length === 0) {
    return <EmptyState icon="file-text" title="No documents yet" description="Uploaded SOPs and policies will appear here." />
  }

  return (
    <>
      <DataTable caption="Documents" columns={columns(setSelected)} rows={query.data.items} rowKey={(row) => row.id} />
      {selected && (
        <DocumentVersions
          documentId={selected.id}
          documentLabel={selected.title}
          timeZone={factory.timezone}
          onClose={() => {
            setSelected(null)
          }}
        />
      )}
    </>
  )
}

function KnowledgeWorkspace() {
  const can = useCan()
  const { showToast } = useToast()

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="knowledge-documents" className="flex flex-col gap-3">
        <h2 id="knowledge-documents" className="text-base font-semibold">
          Documents
        </h2>
        <DocumentList />
      </section>

      {can('document:upload') && (
        <section aria-labelledby="knowledge-upload" className="panel p-6">
          <h2 id="knowledge-upload" className="mb-4 text-base font-semibold">
            Upload a document
          </h2>
          <UploadForm
            onUploaded={(result) => {
              showToast(`${result.slug} uploaded (version ${result.version_no}).`)
            }}
          />
        </section>
      )}

      <section aria-labelledby="knowledge-search" className="flex flex-col gap-3">
        <h2 id="knowledge-search" className="text-base font-semibold">
          Search
        </h2>
        <SearchPanel />
      </section>
    </div>
  )
}

export function KnowledgePage() {
  const can = useCan()
  return (
    <>
      <PageHeader title="Knowledge base" description="SOPs, quality policies and IE standards, with hybrid search over their content." />
      {can('document:read') ? (
        <KnowledgeWorkspace />
      ) : (
        <PermissionDenied message="Your role does not include access to the knowledge base." />
      )}
    </>
  )
}
