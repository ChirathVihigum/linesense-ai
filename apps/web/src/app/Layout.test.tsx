import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { browserNavigation } from '../lib/api'
import { LAST_FACTORY_STORAGE_KEY } from '../lib/factory'
import { renderRoute } from '../test/render'
import { OTHER_FACTORY_ID, makeMe, server, viewerMe } from '../test/server'

describe('Layout', () => {
  it('shows the product, a skip link, and navigation limited to permitted actions', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(viewerMe)))
    renderRoute('/f/F1/orders')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(screen.getByText('LineSense AI')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute('href', '#main-content')
    expect(within(nav).getByRole('link', { name: 'All orders' })).toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'New order' })).not.toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Import orders' })).not.toBeInTheDocument()
  })

  it('hides a whole section when the user cannot read it', async () => {
    const me = makeMe(['org_admin'], ['audit:read', 'admin:manage'])
    server.use(http.get('/api/v1/me', () => HttpResponse.json(me)))
    renderRoute('/f/F1/orders')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).queryByText('Orders')).not.toBeInTheDocument()
    expect(within(nav).queryAllByRole('link')).toHaveLength(0)
  })

  it('lists only permitted factories and switches factory keeping the section', async () => {
    let requestedFactory: string | undefined
    server.use(
      http.get('/api/v1/factories/:factoryId/orders', ({ params }) => {
        requestedFactory = params.factoryId as string
        return HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })
      }),
    )
    const { user, router } = renderRoute('/f/F1/orders')
    const selector = await screen.findByRole('combobox', { name: 'Factory' })
    expect(within(selector).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Factory One (F1)',
      'Factory Two (F2)',
    ])
    await user.selectOptions(selector, 'F2')
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/f/F2/orders')
    })
    await waitFor(() => {
      expect(requestedFactory).toBe(OTHER_FACTORY_ID)
    })
    expect(window.localStorage.getItem(LAST_FACTORY_STORAGE_KEY)).toBe('F2')
  })

  it('shows the user roles for the factory and signs out with a CSRF-protected POST', async () => {
    let csrf: string | null = null
    server.use(
      http.post('/auth/logout', ({ request }) => {
        csrf = request.headers.get('X-CSRF-Token')
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const assign = vi.spyOn(browserNavigation, 'assign').mockImplementation(() => undefined)
    const { user } = renderRoute('/f/F1/orders')
    await user.click(await screen.findByRole('button', { name: 'Pat Planner' }))
    expect(screen.getByRole('list', { name: 'Roles at Factory One' })).toHaveTextContent('Planner')
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith('/login')
    })
    expect(csrf).toBe('csrf-test-token')
  })

  it('redirects / to the remembered factory when it is still permitted', async () => {
    window.localStorage.setItem(LAST_FACTORY_STORAGE_KEY, 'F2')
    const { router } = renderRoute('/')
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/f/F2/orders')
    })
  })

  it('redirects / to /no-access when the user has no factory', async () => {
    server.use(
      http.get('/api/v1/me', () => HttpResponse.json({ ...viewerMe, factories: [] })),
    )
    renderRoute('/')
    expect(await screen.findByRole('heading', { name: 'No factory access' })).toBeInTheDocument()
  })
})
