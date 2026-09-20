import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { page, server } from '../../test/server'

const LITERAL_TEXT = 'Use <b>tag 12A</b> & check the <script>alert(1)</script> label before cutting.'

describe('SearchPanel', () => {
  it('renders excerpt text literally, never as markup', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/documents', () => HttpResponse.json(page([]))),
      http.get('/api/v1/factories/:factoryId/search', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('q')).toBe('tag 12A')
        expect(url.searchParams.get('mode')).toBe('hybrid')
        return HttpResponse.json({
          query: 'tag 12A',
          mode: 'hybrid',
          items: [
            {
              chunk_id: '11111111-1111-4111-8111-111111111111',
              document_id: '22222222-2222-4222-8222-222222222222',
              document_slug: 'cutting-sop',
              document_version_id: '33333333-3333-4333-8333-333333333333',
              version_no: 2,
              title: 'Cutting SOP',
              page_number: 4,
              section: 'Marking',
              text: LITERAL_TEXT,
              score: 0.8231,
              lexical_rank: 1,
              vector_rank: 2,
            },
          ],
        })
      }),
    )

    const { user, container } = renderRoute('/f/F1/knowledge')
    const input = await screen.findByLabelText('Search the knowledge base')
    await user.type(input, 'tag 12A')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText(LITERAL_TEXT)).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText('Cutting SOP · v2')).toBeInTheDocument()
    expect(screen.getByText('Score 0.823')).toBeInTheDocument()
  })

  it('shows an empty state for no matches', async () => {
    server.use(
      http.get('/api/v1/factories/:factoryId/documents', () => HttpResponse.json(page([]))),
      http.get('/api/v1/factories/:factoryId/search', () =>
        HttpResponse.json({ query: 'nothing', mode: 'hybrid', items: [] }),
      ),
    )
    const { user } = renderRoute('/f/F1/knowledge')
    const input = await screen.findByLabelText('Search the knowledge base')
    await user.type(input, 'nothing')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('No matches')).toBeInTheDocument()
  })
})
