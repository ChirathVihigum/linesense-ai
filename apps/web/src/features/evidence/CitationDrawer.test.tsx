import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { server } from '../../test/server'
import { CitationDrawer } from './CitationDrawer'

const SCRIPT_TEXT = '<script>window.pwned = true</script> the chunk text'

function renderDrawer() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <CitationDrawer chunkId="chunk-1" factoryId="factory-1" onClose={() => {}} />
    </QueryClientProvider>,
  )
}

describe('CitationDrawer', () => {
  it('renders a chunk\'s text literally, never creating a script element', async () => {
    server.use(
      http.get('/api/v1/citations/:chunkId', () =>
        HttpResponse.json({
          chunk_id: 'chunk-1',
          document: { id: 'doc-1', slug: 'sop-1', title: 'Cutting SOP' },
          version_no: 1,
          status: 'ACTIVE',
          page_number: 2,
          section: 'Safety',
          text: SCRIPT_TEXT,
        }),
      ),
    )
    renderDrawer()
    expect(await screen.findByText(SCRIPT_TEXT)).toBeInTheDocument()
    expect(document.querySelectorAll('script')).toHaveLength(0)
  })

  it('shows an error state when the citation cannot be loaded', async () => {
    server.use(
      http.get('/api/v1/citations/:chunkId', () =>
        HttpResponse.json(
          { error: { code: 'NOT_FOUND', message: 'The citation was not found.', field_errors: [] } },
          { status: 404 },
        ),
      ),
    )
    renderDrawer()
    expect(await screen.findByText('The citation was not found.')).toBeInTheDocument()
  })
})
