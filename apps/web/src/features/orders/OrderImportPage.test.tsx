import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { File as NodeFile } from 'node:buffer'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderRoute } from '../../test/render'
import { FACTORY_ID, server, viewerMe } from '../../test/server'

const BATCH_ID = '99999999-9999-4999-8999-999999999999'
const CSV = 'external_ref,customer_code,style_code,quantity,due_date,priority\r\nPO-3001,CUST-001,STY-001,500,2026-12-31,3\r\n'

function batch(overrides: Record<string, unknown> = {}) {
  return {
    batch_id: BATCH_ID,
    status: 'VALIDATED',
    row_count: 2,
    errors: [],
    preview: [
      { external_ref: 'PO-3001', customer_code: 'CUST-001', style_code: 'STY-001', quantity: '500', due_date: '2026-12-31', priority: '3' },
      { external_ref: 'PO-3002', customer_code: 'CUST-001', style_code: 'STY-001', quantity: '250', due_date: '2027-01-15', priority: '2' },
    ],
    created_at: '2026-09-18T04:30:00Z',
    committed_at: null,
    ...overrides,
  }
}

/*
 * Test-environment workaround, not app behaviour: Vitest 5's jsdom fetch bridge
 * cannot serialise jsdom Blob/File/FormData bodies with jsdom 30 (it reads a
 * removed internal field). Multipart bodies in these tests therefore use Node's
 * own File and FormData, which Node's fetch/Request (used by MSW) accept directly.
 */
const NodeFormData = (await new Response(new URLSearchParams('probe=1')).formData()).constructor as typeof FormData

function csvFile(content = CSV, name = 'orders.csv'): File {
  return new NodeFile([content], name, { type: 'text/csv' }) as unknown as File
}

describe('OrderImportPage', () => {
  beforeEach(() => {
    vi.stubGlobal('FormData', NodeFormData)
    return () => {
      vi.unstubAllGlobals()
    }
  })

  it('validates a CSV, shows the preview, and commits only on explicit confirmation', async () => {
    const uploads: { key: string | null; fileName: string | undefined }[] = []
    const commits: (string | null)[] = []
    server.use(
      http.post('/api/v1/factories/:factoryId/imports/orders', async ({ request, params }) => {
        expect(params.factoryId).toBe(FACTORY_ID)
        // Read the raw multipart body (the jsdom test environment cannot parse it back into File objects).
        const body = await request.text()
        expect(request.headers.get('Content-Type')).toMatch(/^multipart\/form-data; boundary=/)
        expect(body).toContain('PO-3001,CUST-001,STY-001,500,2026-12-31,3')
        uploads.push({
          key: request.headers.get('Idempotency-Key'),
          fileName: /name="file"; filename="([^"]+)"/.exec(body)?.[1],
        })
        return HttpResponse.json(batch(), { status: 201 })
      }),
      http.post('/api/v1/imports/:batchId/commit', ({ request, params }) => {
        expect(params.batchId).toBe(BATCH_ID)
        commits.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json(batch({ status: 'COMMITTED', committed_at: '2026-09-18T04:35:00Z' }))
      }),
    )
    const { user } = renderRoute('/f/F1/orders/import')

    expect(await screen.findByRole('link', { name: /Download template/ })).toHaveAttribute(
      'href',
      '/api/v1/imports/templates/orders.csv',
    )
    const commitButton = screen.getByRole('button', { name: 'Commit import' })
    expect(commitButton).toBeDisabled()

    await user.upload(screen.getByLabelText(/CSV file/), csvFile())
    await user.click(screen.getByRole('button', { name: 'Validate file' }))

    const preview = await screen.findByRole('table', { name: 'Import preview' })
    expect(within(preview).getByText('PO-3001')).toBeInTheDocument()
    expect(within(preview).getByText('PO-3002')).toBeInTheDocument()
    expect(screen.getByText('Validated')).toBeInTheDocument()
    expect(uploads).toEqual([{ key: expect.stringMatching(/^[0-9a-f-]{36}$/) as string, fileName: 'orders.csv' }])
    expect(commits).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Commit import' }))
    const dialog = screen.getByRole('dialog', { name: 'Create 2 orders?' })
    await user.click(within(dialog).getByRole('button', { name: 'Create orders' }))

    expect(await screen.findByText('2 orders imported.')).toBeInTheDocument()
    expect(commits).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Commit import' })).toBeDisabled()
  })

  it('shows row errors for a rejected file and keeps Commit disabled', async () => {
    server.use(
      http.post('/api/v1/factories/:factoryId/imports/orders', () =>
        HttpResponse.json(
          batch({
            status: 'REJECTED',
            errors: [
              { row_number: 2, field: 'quantity', message: 'quantity must be an integer.' },
              { row_number: 3, field: 'customer_code', message: 'Unknown customer code CUST-404.' },
            ],
          }),
          { status: 201 },
        ),
      ),
    )
    const { user } = renderRoute('/f/F1/orders/import')
    await user.upload(await screen.findByLabelText(/CSV file/), csvFile())
    await user.click(screen.getByRole('button', { name: 'Validate file' }))

    const errors = await screen.findByRole('table', { name: 'Row errors' })
    expect(within(errors).getByText('quantity must be an integer.')).toBeInTheDocument()
    expect(within(errors).getByText('Unknown customer code CUST-404.')).toBeInTheDocument()
    expect(screen.getByText('Rejected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Commit import' })).toBeDisabled()
  })

  it('rejects files over 1 MB or without a .csv extension before uploading', async () => {
    let uploaded = false
    server.use(
      http.post('/api/v1/factories/:factoryId/imports/orders', () => {
        uploaded = true
        return HttpResponse.json(batch(), { status: 201 })
      }),
    )
    const { user } = renderRoute('/f/F1/orders/import')
    const input = await screen.findByLabelText(/CSV file/)

    await user.upload(input, csvFile('x'.repeat(1_048_577)))
    expect(await screen.findByText('The file is larger than 1 MB.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Validate file' })).toBeDisabled()

    await user.upload(input, csvFile('a,b', 'orders.txt'))
    expect(await screen.findByText('Choose a .csv file.')).toBeInTheDocument()
    expect(uploaded).toBe(false)
  })

  it('shows permission denied to users who cannot import', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/orders/import')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
  })
})
