import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { plannerMe, server, viewerMe } from '../../test/server'

const LINE_ID = 'line-1'
const STYLE_ID = 'style-1'

function mockAnalysisRoutes() {
  server.use(
    http.get('/api/v1/factories/:factoryId/lines', () =>
      HttpResponse.json({ items: [{ id: LINE_ID, code: 'L1', name: 'Line 1', operator_count: 20, is_active: true, skill_codes: [] }], total: 1, limit: 200, offset: 0 }),
    ),
    http.get('/api/v1/factories/:factoryId/styles', () =>
      HttpResponse.json({ items: [{ id: STYLE_ID, code: 'STY-1', name: 'Crew tee', product_type: 'T-shirt' }], total: 1, limit: 200, offset: 0 }),
    ),
    http.get('/api/v1/factories/:factoryId/ie/lines/:lineId/styles/:styleId/analysis', () =>
      HttpResponse.json({
        line_id: LINE_ID,
        style_id: STYLE_ID,
        operations: [
          {
            operation_id: 'op-1',
            code: 'OP-01',
            name: 'collar attach',
            sam_minutes: '0.5000',
            sample_count: 2,
            representative_seconds: '30.00',
            parallel_operators: 1,
            effective_seconds: '30.00',
            insufficient_samples: true,
          },
          {
            operation_id: 'op-4',
            code: 'OP-04',
            name: 'sleeve set',
            sam_minutes: '1.0000',
            sample_count: 8,
            representative_seconds: '60.00',
            parallel_operators: 1,
            effective_seconds: '60.00',
            insufficient_samples: false,
          },
        ],
        balance: { bottleneck_index: 1, bottleneck_effective_seconds: '60.00', units_per_hour: '60.00', balance_index_percent: '50.00' },
        observed_units_per_hour: '58.00',
        sam_units_per_hour: '60.00',
        assumptions: ['Cycle times assume standard operating procedure.'],
        limitations: ['Fewer than 5 samples for OP-01.'],
        data_versions: {},
      }),
    ),
    http.get('/api/v1/factories/:factoryId/ie/operator-aliases', () =>
      HttpResponse.json({ items: [], total: 0, limit: 200, offset: 0 }),
    ),
  )
}

describe('IEPage', () => {
  it('shows the bottleneck as a text summary alongside the chart and table', async () => {
    mockAnalysisRoutes()
    const { user } = renderRoute('/f/F1/ie')
    const lineSelect = await screen.findByLabelText('Line')
    await screen.findByRole('option', { name: 'Line 1 (L1)' })
    await user.selectOptions(lineSelect, LINE_ID)
    await screen.findByRole('option', { name: 'Crew tee (STY-1)' })
    await user.selectOptions(screen.getByLabelText('Style'), STYLE_ID)

    expect(
      await screen.findByText('Bottleneck: OP-04 sleeve set, 60.0 s effective, ≈ 60 units/hour'),
    ).toBeInTheDocument()
    expect(screen.getByText('Insufficient samples')).toBeInTheDocument()
    expect(screen.getByText('Cycle times assume standard operating procedure.')).toBeInTheDocument()
    expect(screen.getByText('Fewer than 5 samples for OP-01.')).toBeInTheDocument()
  })

  it('hides the observation form for a planner (no ie:write)', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json(plannerMe)))
    mockAnalysisRoutes()
    const { user } = renderRoute('/f/F1/ie')
    const lineSelect = await screen.findByLabelText('Line')
    await screen.findByRole('option', { name: 'Line 1 (L1)' })
    await user.selectOptions(lineSelect, LINE_ID)
    await screen.findByRole('option', { name: 'Crew tee (STY-1)' })
    await user.selectOptions(screen.getByLabelText('Style'), STYLE_ID)
    await screen.findByText(/Bottleneck: OP-04/)
    expect(screen.queryByText('Record a cycle observation')).not.toBeInTheDocument()
  })

  it('denies the whole page to a role without ie:read', async () => {
    server.use(http.get('/api/v1/me', () => HttpResponse.json({ ...viewerMe, factories: viewerMe.factories.map((f) => ({ ...f, permissions: f.permissions.filter((p) => p !== 'ie:read') })) })))
    renderRoute('/f/F1/ie')
    expect(await screen.findByText('Permission required')).toBeInTheDocument()
  })
})
