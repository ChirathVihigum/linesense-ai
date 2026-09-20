import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { errorBody, makeOrderDetail, server } from '../../test/server'

describe('OrderDetailPage', () => {
  it('shows a stale banner when a transition returns 409 STALE_INPUT', async () => {
    server.use(
      http.get('/api/v1/orders/:orderId', () =>
        HttpResponse.json(makeOrderDetail({ allowed_transitions: ['VALIDATED'] })),
      ),
      http.post('/api/v1/orders/:orderId/transitions', () =>
        HttpResponse.json(
          errorBody('STALE_INPUT', 'The order has changed (version 4); reload and try again.'),
          { status: 409 },
        ),
      ),
    )
    const { user } = renderRoute('/f/F1/orders/77777777-7777-4777-8777-777777777777')
    await user.click(await screen.findByRole('button', { name: /Move to validated/i }))
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('Possibly out of date.')).toBeInTheDocument()
    expect(screen.getByText('This order has changed.')).toBeInTheDocument()
  })

  it('lists audit events in the History tab', async () => {
    server.use(
      http.get('/api/v1/orders/:orderId', () => HttpResponse.json(makeOrderDetail())),
      http.get('/api/v1/orders/:orderId/history', () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              actor_display_name: 'Jordan Supervisor',
              action: 'order.transition',
              outcome: 'SUCCESS',
              reason: null,
              created_at: '2026-09-20T05:00:00Z',
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
    )
    const { user } = renderRoute('/f/F1/orders/77777777-7777-4777-8777-777777777777')
    await screen.findByText('PO-1001')
    await user.click(screen.getByRole('tab', { name: 'History' }))
    expect(await screen.findByText('Jordan Supervisor')).toBeInTheDocument()
    expect(screen.getByText('Order transition')).toBeInTheDocument()
  })
})
