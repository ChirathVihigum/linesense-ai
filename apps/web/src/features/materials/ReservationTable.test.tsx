import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { server, storekeeperMe } from '../../test/server'

const RESERVATION_ID = 'reservation-1'

function mockBaseRoutes() {
  server.use(
    http.get('/api/v1/me', () => HttpResponse.json(storekeeperMe)),
    http.get('/api/v1/factories/:factoryId/materials', () =>
      HttpResponse.json({ as_of: '2026-09-20', items: [] }),
    ),
    http.get('/api/v1/factories/:factoryId/reservations', () =>
      HttpResponse.json({
        items: [
          {
            id: RESERVATION_ID,
            material_id: 'material-1',
            order_id: 'order-1',
            quantity: '50.0000',
            status: 'ACTIVE',
            recommendation_id: null,
            created_by: null,
            created_at: '2026-09-20T00:00:00Z',
          },
        ],
        total: 1,
        limit: 20,
        offset: 0,
      }),
    ),
  )
}

describe('ReservationTable', () => {
  it('closes the confirm dialog and shows the server error when release fails', async () => {
    mockBaseRoutes()
    server.use(
      http.post('/api/v1/reservations/:reservationId/release', () =>
        HttpResponse.json(
          {
            error: {
              code: 'CONFLICT',
              message: 'This reservation was already released.',
              field_errors: [],
              trace_id: 'trace-1',
              retry_after_seconds: null,
            },
          },
          { status: 409 },
        ),
      ),
    )
    const { user } = renderRoute('/f/F1/materials')
    await user.click(await screen.findByRole('button', { name: 'Release' }))

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Release' }))

    // The dialog closes so the error underneath is not hidden behind the overlay.
    expect(await screen.findByText('This reservation was already released.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
