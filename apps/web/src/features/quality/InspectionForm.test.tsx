import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { qualityManagerMe, server } from '../../test/server'

const ORDER_ID = 'order-1'

function baseOrderQuality(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    order_id: ORDER_ID,
    order_version: 3,
    quality_state: 'PENDING',
    inspections: [],
    holds: [],
    releases: [],
    shipment: { eligible: false, reasons: ['No FINAL inspection has passed.'] },
    policy: { id: 'policy-1', code: 'DEMO', version_no: 1, is_demo: true, status: 'ACTIVE', rules: {}, label: 'Demo policy — not a certified AQL standard' },
    ...overrides,
  }
}

function mockQualityRoutes() {
  server.use(
    http.get('/api/v1/me', () => HttpResponse.json(qualityManagerMe)),
    http.get('/api/v1/factories/:factoryId/quality/holds', () =>
      HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
    ),
    http.get('/api/v1/factories/:factoryId/quality/trends', () =>
      HttpResponse.json({
        factory_id: 'f1',
        window_days: 30,
        inspected_units: 0,
        defective_units: 0,
        defective_rate: null,
        defects_per_hundred_units: null,
        by_defect_code: [],
      }),
    ),
    http.get('/api/v1/factories/:factoryId/orders', () =>
      HttpResponse.json({
        items: [{ id: ORDER_ID, external_ref: 'PO-3001', customer: { id: 'c1', code: 'C1', name: 'Acme' }, style: { id: 's1', code: 'S1', name: 'Tee' }, quantity: 100, produced_units: 100, packed_units: 0, due_date: '2026-10-01', priority: 3, production_state: 'IN_PRODUCTION', material_state: 'READY', quality_state: 'PENDING', shipment: { eligible: false, reasons: [] }, version: 3, updated_at: '2026-09-20T00:00:00Z' }],
        total: 1,
        limit: 5,
        offset: 0,
      }),
    ),
    http.get('/api/v1/orders/:orderId/quality', () => HttpResponse.json(baseOrderQuality())),
  )
}

async function selectOrder(user: ReturnType<typeof renderRoute>['user']) {
  await user.type(await screen.findByLabelText(/Find an order by reference/), 'PO-3001')
  const option = await screen.findByRole('button', { name: /PO-3001/ })
  await user.click(option)
  await screen.findByText('Record inspection', { selector: 'h3' })
}

describe('InspectionForm', () => {
  it('rejects defective units greater than inspected units without calling the server', async () => {
    mockQualityRoutes()
    let posted = false
    server.use(
      http.post('/api/v1/orders/:orderId/inspections', () => {
        posted = true
        return HttpResponse.json(baseOrderQuality(), { status: 201 })
      }),
    )
    const { user } = renderRoute('/f/F1/quality')
    await selectOrder(user)

    await user.clear(screen.getByLabelText(/Inspected units/))
    await user.type(screen.getByLabelText(/Inspected units/), '10')
    await user.clear(screen.getByLabelText(/Defective units/))
    await user.type(screen.getByLabelText(/Defective units/), '20')
    await user.click(screen.getByRole('button', { name: 'Record inspection' }))

    expect(await screen.findByText('Defective units cannot exceed inspected units.')).toBeInTheDocument()
    expect(posted).toBe(false)
  })

  it('shows the deterministic result the server returns and sends an Idempotency-Key', async () => {
    mockQualityRoutes()
    let idempotencyKey: string | null = null
    server.use(
      http.post('/api/v1/orders/:orderId/inspections', ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(
          baseOrderQuality({
            inspections: [
              {
                id: 'insp-1',
                order_id: ORDER_ID,
                line_id: null,
                inspection_type: 'FINAL',
                inspected_units: 100,
                defective_units: 2,
                policy_version_id: 'policy-1',
                result: 'PASS',
                inspected_by: 'inspector-1',
                inspected_at: '2026-09-20T00:00:00Z',
                defects: [],
              },
            ],
          }),
          { status: 201 },
        )
      }),
    )
    const { user } = renderRoute('/f/F1/quality')
    await selectOrder(user)

    await user.clear(screen.getByLabelText(/Inspected units/))
    await user.type(screen.getByLabelText(/Inspected units/), '100')
    await user.clear(screen.getByLabelText(/Defective units/))
    await user.type(screen.getByLabelText(/Defective units/), '2')
    await user.click(screen.getByRole('button', { name: 'Record inspection' }))

    expect(await screen.findByText('Result: Pass')).toBeInTheDocument()
    await waitFor(() => {
      expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
    })
  })
})
