import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { qualityManagerMe, server } from '../../test/server'

const ORDER_ID = 'order-1'
const OTHER_USER_ID = '99999999-9999-4999-8999-999999999999'

function orderQuality(inspectedBy: string | null) {
  return {
    order_id: ORDER_ID,
    order_version: 5,
    quality_state: 'PENDING',
    inspections: [
      {
        id: 'insp-1',
        order_id: ORDER_ID,
        line_id: null,
        inspection_type: 'FINAL',
        inspected_units: 100,
        defective_units: 1,
        policy_version_id: 'policy-1',
        result: 'PASS',
        inspected_by: inspectedBy,
        inspected_at: '2026-09-20T00:00:00Z',
        defects: [],
      },
    ],
    holds: [],
    releases: [],
    shipment: { eligible: false, reasons: ['Awaiting quality release.'] },
    policy: {
      id: 'policy-1',
      code: 'DEMO',
      version_no: 1,
      is_demo: true,
      status: 'ACTIVE',
      rules: {},
      label: 'Demo policy — not a certified AQL standard',
    },
  }
}

function mockQualityRoutes(inspectedBy: string | null) {
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
        items: [
          {
            id: ORDER_ID,
            external_ref: 'PO-3001',
            customer: { id: 'c1', code: 'C1', name: 'Acme' },
            style: { id: 's1', code: 'S1', name: 'Tee' },
            quantity: 100,
            produced_units: 100,
            packed_units: 0,
            due_date: '2026-10-01',
            priority: 3,
            production_state: 'IN_PRODUCTION',
            material_state: 'READY',
            quality_state: 'PENDING',
            shipment: { eligible: false, reasons: [] },
            version: 5,
            updated_at: '2026-09-20T00:00:00Z',
          },
        ],
        total: 1,
        limit: 5,
        offset: 0,
      }),
    ),
    http.get('/api/v1/orders/:orderId/quality', () => HttpResponse.json(orderQuality(inspectedBy))),
  )
}

async function selectOrder(user: ReturnType<typeof renderRoute>['user']) {
  await user.type(await screen.findByLabelText(/Find an order by reference/), 'PO-3001')
  const option = await screen.findByRole('button', { name: /PO-3001/ })
  await user.click(option)
  await screen.findByText('Release for shipment', { selector: 'h3' })
}

describe('ReleaseReview', () => {
  it('shows the demo-policy label and allows release when the viewer did not inspect', async () => {
    mockQualityRoutes(OTHER_USER_ID)
    const { user } = renderRoute('/f/F1/quality')
    await selectOrder(user)

    expect(screen.getByText('Demo policy — not a certified AQL standard')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Release for shipment' })).toBeEnabled()
  })

  it('blocks release and explains separation of duties when the viewer was the inspector', async () => {
    mockQualityRoutes(qualityManagerMe.user.id)
    const { user } = renderRoute('/f/F1/quality')
    await selectOrder(user)

    expect(
      screen.getByText('You inspected this order. Separation of duties requires a different person to release it.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Release for shipment' })).toBeDisabled()
  })
})
