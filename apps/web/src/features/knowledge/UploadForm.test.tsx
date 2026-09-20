import { fireEvent, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderRoute } from '../../test/render'
import { errorBody, makeMe, page, server } from '../../test/server'

const uploaderMe = makeMe(['supervisor'], ['document:read', 'document:upload', 'order:read'])

function pdfFile(name = 'policy.pdf', sizeBytes = 1024): File {
  return new File([new Uint8Array(sizeBytes)], name, { type: 'application/pdf' })
}

/**
 * A minimal `XMLHttpRequest` stand-in. `UploadForm` uses real XHR (not
 * `fetch`) so upload progress is observable; MSW's node interceptors patch
 * `fetch`/`http`, not jsdom's XHR implementation, so these tests drive the
 * upload's request/response cycle directly instead.
 */
class FakeXhr {
  static instances: FakeXhr[] = []
  method = ''
  url = ''
  withCredentials = false
  status = 0
  responseText = ''
  headers: Record<string, string> = {}
  body: FormData | null = null
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null

  open(method: string, url: string): void {
    this.method = method
    this.url = url
    FakeXhr.instances.push(this)
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value
  }

  send(body: FormData): void {
    this.body = body
  }

  respond(status: number, body: unknown): void {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.onload?.()
  }
}

afterEach(() => {
  FakeXhr.instances = []
  vi.unstubAllGlobals()
})

describe('UploadForm', () => {
  it('rejects an oversize file client-side, without ever calling the server', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    server.use(
      http.get('/api/v1/me', () => HttpResponse.json(uploaderMe)),
      http.get('/api/v1/factories/:factoryId/documents', () => HttpResponse.json(page([]))),
    )

    const { user } = renderRoute('/f/F1/knowledge')
    const fileInput = await screen.findByLabelText('File')
    await user.upload(fileInput, pdfFile('policy.pdf', 11 * 1024 * 1024))

    expect(await screen.findByText('The file is larger than 10 MB.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload document' })).toBeDisabled()
    expect(FakeXhr.instances).toHaveLength(0)
  })

  it('rejects a disallowed file extension client-side (e.g. dropped past the file picker filter)', async () => {
    server.use(
      http.get('/api/v1/me', () => HttpResponse.json(uploaderMe)),
      http.get('/api/v1/factories/:factoryId/documents', () => HttpResponse.json(page([]))),
    )
    renderRoute('/f/F1/knowledge')
    const fileInput = await screen.findByLabelText('File')
    fireEvent.change(fileInput, {
      target: { files: [new File(['data'], 'notes.docx', { type: 'application/msword' })] },
    })
    expect(await screen.findByText('Choose a .pdf, .md, .txt file.')).toBeInTheDocument()
  })

  it('shows the server 415 error for a file the server rejects', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    server.use(
      http.get('/api/v1/me', () => HttpResponse.json(uploaderMe)),
      http.get('/api/v1/factories/:factoryId/documents', () => HttpResponse.json(page([]))),
    )
    const { user } = renderRoute('/f/F1/knowledge')
    await user.type(await screen.findByLabelText(/Title/), 'Policy')
    await user.type(screen.getByLabelText(/Slug/), 'policy-1')
    await user.upload(screen.getByLabelText('File'), pdfFile())
    await user.click(screen.getByRole('button', { name: 'Upload document' }))

    await vi.waitFor(() => {
      expect(FakeXhr.instances).toHaveLength(1)
    })
    const request = FakeXhr.instances[0]
    if (!request) throw new Error('expected an XHR request to have been opened')
    expect(request.method).toBe('POST')
    expect(request.url).toBe('/api/v1/factories/11111111-1111-4111-8111-111111111111/documents')
    expect(request.headers['Idempotency-Key']).toBeTruthy()
    request.respond(415, errorBody('UNSUPPORTED_MEDIA_TYPE', 'The file content does not match its extension.'))

    expect(await screen.findByText('The file content does not match its extension.')).toBeInTheDocument()
  })
})
