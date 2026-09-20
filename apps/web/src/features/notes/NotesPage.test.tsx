import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { errorBody, page, server, viewerMe } from '../../test/server'

const NOTE_RESPONSE = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  text: 'PO-1001 is delayed on Line A due to a shortage of fabric-01.',
  classification: 'planning',
  classifier_version: 'tfidf-v1',
  entities: [
    {
      label: 'ORDER',
      text: 'PO-1001',
      start: 0,
      end: 7,
      resolved_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    },
    { label: 'LINE', text: 'Line A', start: 20, end: 26, resolved_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' },
  ],
  unresolved: [{ label: 'ORDER', text: 'PO-9999', start: 0, end: 0, resolved_id: null }],
  created_at: '2026-09-20T05:00:00Z',
  notice: 'Entity links are for navigation only; they do not authorize any action.',
}

describe('NotesPage', () => {
  it('renders entity links and the navigation-only notice after creating a note', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/notes', () => HttpResponse.json(page([]))),
      http.post('/api/v1/factories/:factoryId/notes', () => HttpResponse.json(NOTE_RESPONSE, { status: 201 })),
    )
    const { user } = renderRoute('/f/F1/notes')

    const textarea = await screen.findByLabelText('Add a note')
    await user.type(textarea, 'PO-1001 is delayed on Line A due to a shortage of fabric-01.')
    await user.click(screen.getByRole('button', { name: 'Save note' }))

    const result = await screen.findByRole('status', { name: 'Note saved' })
    expect(within(result).getByText('Planning')).toBeInTheDocument()
    expect(within(result).getByText('Classifier tfidf-v1')).toBeInTheDocument()

    const orderLink = within(result).getByRole('link', { name: /Order: PO-1001/ })
    expect(orderLink).toHaveAttribute('href', '/f/F1/orders/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')

    const lineLink = within(result).getByRole('link', { name: /Line: Line A/ })
    expect(lineLink).toHaveAttribute('href', '/f/F1/planning')

    expect(within(result).getByText(/PO-9999/)).toBeInTheDocument()
    expect(
      within(result).getByText('Entity links are for navigation only; they do not authorize any action.'),
    ).toBeInTheDocument()
  })

  it('shows a server validation error inline', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/notes', () => HttpResponse.json(page([]))),
      http.post('/api/v1/factories/:factoryId/notes', () =>
        HttpResponse.json(errorBody('VALIDATION_ERROR', 'Note text is too short.'), { status: 422 }),
      ),
    )
    const { user } = renderRoute('/f/F1/notes')
    const textarea = await screen.findByLabelText('Add a note')
    await user.type(textarea, 'hi there team')
    await user.click(screen.getByRole('button', { name: 'Save note' }))
    expect(await screen.findByText('Note text is too short.')).toBeInTheDocument()
  })

  it('hides the page from users without note:create', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/notes')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
  })
})
