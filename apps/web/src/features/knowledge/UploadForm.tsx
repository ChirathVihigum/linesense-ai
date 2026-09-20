import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { ErrorState } from '../../components/ErrorState'
import { FormField } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import { fieldAria } from '../../components/fieldAria'
import { ApiError, CSRF_HEADER, sessionState, type Schemas } from '../../lib/api'
import { useFactory } from '../../lib/factory'
import { useIdempotencyKey } from '../../lib/idempotency'
import { ROLES } from '../../lib/permissions'

type DocumentUploadResult = Schemas['DocumentUploadResult']

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
const ALLOWED_EXTENSIONS = ['.pdf', '.md', '.txt']
const DOC_TYPES = ['SOP', 'QUALITY_POLICY', 'IE_STANDARD', 'OTHER']
const DEFAULT_DOC_TYPE = DOC_TYPES[0] ?? 'SOP'

function fileExtensionError(file: File): string | null {
  const name = file.name.toLowerCase()
  if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return `Choose a ${ALLOWED_EXTENSIONS.join(', ')} file.`
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return 'The file is larger than 10 MB.'
  }
  return null
}

interface XhrResult {
  status: number
  body: unknown
}

/** `XMLHttpRequest` wrapped in a promise so upload progress is observable
 * (the `fetch`-based generated client has no upload progress event). */
function uploadWithProgress(
  url: string,
  formData: FormData,
  headers: Record<string, string>,
  onProgress: (percent: number) => void,
): Promise<XhrResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        // A non-JSON body (e.g. a proxy error page) is reported as a plain status.
      }
      resolve({ status: xhr.status, body })
    }
    xhr.onerror = () => {
      reject(new Error('Could not reach the LineSense server. Check your connection and try again.'))
    }
    xhr.send(formData)
  })
}

function apiErrorFromResult(result: XhrResult): ApiError {
  const body = result.body as { error?: { code?: string; message?: string; trace_id?: string } } | null
  const error = body?.error
  return new ApiError({
    status: result.status,
    code: error?.code ?? 'UNKNOWN_ERROR',
    message: error?.message ?? `The server responded with status ${result.status}.`,
    traceId: error?.trace_id ?? null,
  })
}

export function UploadForm({ onUploaded }: { onUploaded: (result: DocumentUploadResult) => void }) {
  const factory = useFactory()
  const queryClient = useQueryClient()
  const idempotency = useIdempotencyKey()
  const formId = useId()
  const canUploadOrgWide = factory.roles.includes('org_admin') || factory.roles.includes('supervisor')

  const [title, setTitle] = useState('')
  const [slug, setSlug] = useState('')
  const [docType, setDocType] = useState<string>(DEFAULT_DOC_TYPE)
  const [scope, setScope] = useState<'factory' | 'org'>('factory')
  const [aclRoles, setAclRoles] = useState<string[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [progress, setProgress] = useState<number | null>(null)

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error('Choose a file first.')
      const key = idempotency.keyFor({ slug, size: file.size, modified: file.lastModified })
      const formData = new FormData()
      formData.append('title', title)
      formData.append('slug', slug)
      formData.append('doc_type', docType)
      formData.append('scope', scope)
      formData.append('acl_roles', aclRoles.join(','))
      formData.append('file', file)
      setProgress(0)
      const result = await uploadWithProgress(
        `/api/v1/factories/${factory.id}/documents`,
        formData,
        { [CSRF_HEADER]: sessionState.csrfToken ?? '', 'Idempotency-Key': key },
        setProgress,
      )
      if (result.status < 200 || result.status >= 300) throw apiErrorFromResult(result)
      return result.body as DocumentUploadResult
    },
    onSuccess: async (result) => {
      idempotency.reset()
      setTitle('')
      setSlug('')
      setFile(null)
      setProgress(null)
      onUploaded(result)
      await queryClient.invalidateQueries({ queryKey: ['documents', factory.id] })
    },
    onError: () => {
      setProgress(null)
    },
  })

  return (
    <form
      className="grid grid-cols-1 gap-5 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (file) {
          const clientError = fileExtensionError(file)
          if (clientError) {
            setFileError(clientError)
            return
          }
        }
        upload.mutate()
      }}
    >
      <FormField id={`${formId}-title`} label="Title" required>
        <input
          id={`${formId}-title`}
          className="input"
          value={title}
          required
          onChange={(event) => {
            setTitle(event.target.value)
          }}
        />
      </FormField>
      <FormField id={`${formId}-slug`} label="Slug" hint="Lowercase, unique within the organization." required>
        <input
          id={`${formId}-slug`}
          className="input"
          value={slug}
          required
          onChange={(event) => {
            setSlug(event.target.value)
          }}
        />
      </FormField>
      <FormField id={`${formId}-type`} label="Document type">
        <select
          id={`${formId}-type`}
          className="input"
          value={docType}
          onChange={(event) => {
            setDocType(event.target.value)
          }}
        >
          {DOC_TYPES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </FormField>
      <FormField id={`${formId}-scope`} label="Scope">
        <select
          id={`${formId}-scope`}
          className="input"
          value={scope}
          onChange={(event) => {
            setScope(event.target.value as 'factory' | 'org')
          }}
        >
          <option value="factory">This factory only</option>
          {canUploadOrgWide && <option value="org">Every factory in the organization</option>}
        </select>
      </FormField>
      <fieldset className="sm:col-span-2">
        <legend className="mb-1.5 font-medium">Visible to roles</legend>
        <p className="mb-2 text-xs text-fg-muted">Leave every role unchecked to make it visible to every role with document access.</p>
        <div className="flex flex-wrap gap-3">
          {ROLES.map((role) => (
            <label key={role} className="inline-flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={aclRoles.includes(role)}
                onChange={(event) => {
                  setAclRoles((current) =>
                    event.target.checked ? [...current, role] : current.filter((value) => value !== role),
                  )
                }}
              />
              {role}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="sm:col-span-2">
        <FormField
          id={`${formId}-file`}
          label="File"
          hint="PDF, Markdown or plain text, up to 10 MB."
          error={fileError ?? undefined}
        >
          <input
            type="file"
            accept=".pdf,.md,.txt"
            className="block w-full text-sm file:mr-3 file:h-9 file:rounded-md file:border file:border-line-strong/60 file:bg-surface file:px-3 file:text-sm file:font-medium file:text-fg"
            {...fieldAria(`${formId}-file`, fileError ?? undefined, `${formId}-file-hint`)}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null
              setFile(selected)
              setFileError(selected ? fileExtensionError(selected) : null)
            }}
          />
        </FormField>
      </div>
      {upload.isError && <ErrorState error={upload.error} title="The document was not uploaded" />}
      {progress !== null && (
        <p className="sm:col-span-2 text-sm text-fg-muted" role="status">
          Uploading… {progress}%
        </p>
      )}
      <div className="sm:col-span-2">
        <button
          type="submit"
          className="btn-primary"
          disabled={!title || !slug || !file || fileError !== null || upload.isPending}
        >
          <Icon name="upload" />
          {upload.isPending ? 'Uploading…' : 'Upload document'}
        </button>
      </div>
    </form>
  )
}
