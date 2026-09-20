import { screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { page, server } from '../../test/server'

function notification(overrides: Record<string, unknown> = {}) {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    kind: 'test',
    title: 'Order at risk',
    body: 'PO-1001 is short on fabric.',
    link: null,
    created_at: '2026-09-20T05:00:00Z',
    read_at: null,
    ...overrides,
  }
}

describe('NotificationsMenu', () => {
  it('shows an unread badge, opens the list, and marks an item read on click', async () => {
    let readCalls = 0
    server.use(
      http.get('/api/v1/factories/:factoryId/notifications', () => HttpResponse.json(page([notification()]))),
      http.post('/api/v1/notifications/:id/read', () => {
        readCalls += 1
        return HttpResponse.json(notification({ read_at: '2026-09-20T05:05:00Z' }))
      }),
    )

    const { user } = renderRoute('/f/F1/orders')
    const button = await screen.findByRole('button', { name: 'Notifications, 1 unread' })
    expect(within(button).getByText('1')).toBeInTheDocument()

    await user.click(button)
    const menu = await screen.findByRole('menu', { name: 'Notifications' })
    expect(within(menu).getByText('Order at risk')).toBeInTheDocument()

    await user.click(within(menu).getByText('Order at risk'))
    await waitFor(() => {
      expect(readCalls).toBe(1)
    })
  })

  it('shows no badge when there are no unread notifications', async () => {
    server.use(http.get('/api/v1/factories/:factoryId/notifications', () => HttpResponse.json(page([]))))
    renderRoute('/f/F1/orders')
    expect(await screen.findByRole('button', { name: 'Notifications' })).toBeInTheDocument()
  })

  it('renders a linked notification as a single link, never a link nested in a button', async () => {
    let readCalls = 0
    server.use(
      http.get('/api/v1/factories/:factoryId/notifications', () =>
        HttpResponse.json(page([notification({ link: '/orders/order-1' })])),
      ),
      http.post('/api/v1/notifications/:id/read', () => {
        readCalls += 1
        return HttpResponse.json(notification({ link: '/orders/order-1', read_at: '2026-09-20T05:05:00Z' }))
      }),
    )

    const { user } = renderRoute('/f/F1/orders')
    const button = await screen.findByRole('button', { name: 'Notifications, 1 unread' })
    await user.click(button)
    const menu = await screen.findByRole('menu', { name: 'Notifications' })

    const link = within(menu).getByRole('link', { name: /Order at risk/ })
    expect(link).toHaveAttribute('href', '/f/F1/orders/order-1')
    // Invalid HTML would nest a <button> inside this <a> (or vice versa);
    // neither is present, so this row has exactly one interactive element.
    expect(link.querySelector('button')).toBeNull()
    expect(link.closest('button')).toBeNull()

    await user.click(link)
    await waitFor(() => {
      expect(readCalls).toBe(1)
    })
  })
})
