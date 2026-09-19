import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { ieEngineerMe, server } from '../../test/server'

const LINE_ID = 'line-1'
const STYLE_ID = 'style-1'

function mockIeRoutes() {
  server.use(
    http.get('/api/v1/me', () => HttpResponse.json(ieEngineerMe)),
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
            code: 'OP-04',
            name: 'sleeve set',
            sam_minutes: '1.0000',
            sample_count: 5,
            representative_seconds: '60.00',
            parallel_operators: 1,
            effective_seconds: '60.00',
            insufficient_samples: false,
          },
        ],
        balance: { bottleneck_index: 0, bottleneck_effective_seconds: '60.00', units_per_hour: '60.00', balance_index_percent: '100.00' },
        observed_units_per_hour: '58.00',
        sam_units_per_hour: '60.00',
        assumptions: [],
        limitations: [],
        data_versions: {},
      }),
    ),
    http.get('/api/v1/factories/:factoryId/ie/operator-aliases', () =>
      HttpResponse.json({
        items: [
          { id: 'alias-1', alias_code: 'OP-A1', line_id: LINE_ID, is_active: true },
          { id: 'alias-2', alias_code: 'OP-A2', line_id: null, is_active: true },
        ],
        total: 2,
        limit: 200,
        offset: 0,
      }),
    ),
  )
}

describe('ObservationForm (via IEPage)', () => {
  it('lists operator aliases only, never a person name', async () => {
    mockIeRoutes()
    const { user } = renderRoute(`/f/F1/ie`)
    const lineSelect = await screen.findByLabelText('Line')
    await screen.findByRole('option', { name: 'Line 1 (L1)' })
    await user.selectOptions(lineSelect, LINE_ID)
    await screen.findByRole('option', { name: 'Crew tee (STY-1)' })
    await user.selectOptions(screen.getByLabelText('Style'), STYLE_ID)

    const aliasSelect = await screen.findByLabelText(/Operator alias/)
    const optionTexts = Array.from(aliasSelect.querySelectorAll('option')).map((option) => option.textContent)
    // Only the two pseudonymous alias codes plus the placeholder: no operator's display name.
    expect(optionTexts).toEqual(['Select an alias', 'OP-A1', 'OP-A2'])
  })

  it('records an observation with an Idempotency-Key and lists it for outlier marking', async () => {
    mockIeRoutes()
    let idempotencyKey: string | null = null
    server.use(
      http.post('/api/v1/factories/:factoryId/ie/observations', ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(
          {
            id: 'obs-1',
            line_id: LINE_ID,
            style_id: STYLE_ID,
            operation_id: 'op-1',
            operator_alias_id: 'alias-1',
            observed_seconds: '61.00',
            observed_at: '2026-09-20T10:00:00Z',
            is_outlier: false,
            outlier_approved_by: null,
            recorded_by: null,
            created_at: '2026-09-20T10:00:00Z',
          },
          { status: 201 },
        )
      }),
    )
    const { user } = renderRoute(`/f/F1/ie`)
    const lineSelect = await screen.findByLabelText('Line')
    await screen.findByRole('option', { name: 'Line 1 (L1)' })
    await user.selectOptions(lineSelect, LINE_ID)
    await screen.findByRole('option', { name: 'Crew tee (STY-1)' })
    await user.selectOptions(screen.getByLabelText('Style'), STYLE_ID)

    await user.selectOptions(await screen.findByLabelText(/^Operation/), 'op-1')
    await user.selectOptions(screen.getByLabelText(/Operator alias/), 'OP-A1')
    await user.type(screen.getByLabelText(/Observed cycle time/), '61')
    await user.type(screen.getByLabelText(/Observed at/), '2026-09-20T10:00')
    await user.click(screen.getByRole('button', { name: 'Record observation' }))

    expect(await screen.findByText('Mark as outlier')).toBeInTheDocument()
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('requires a non-blank reason before an observation can be marked an outlier', async () => {
    mockIeRoutes()
    let outlierBody: unknown = null
    server.use(
      http.post('/api/v1/factories/:factoryId/ie/observations', () =>
        HttpResponse.json(
          {
            id: 'obs-1',
            line_id: LINE_ID,
            style_id: STYLE_ID,
            operation_id: 'op-1',
            operator_alias_id: 'alias-1',
            observed_seconds: '61.00',
            observed_at: '2026-09-20T10:00:00Z',
            is_outlier: false,
            outlier_approved_by: null,
            recorded_by: null,
            created_at: '2026-09-20T10:00:00Z',
          },
          { status: 201 },
        ),
      ),
      http.post('/api/v1/ie/observations/:observationId/outlier', async ({ request }) => {
        outlierBody = await request.json()
        return HttpResponse.json(
          {
            id: 'obs-1',
            line_id: LINE_ID,
            style_id: STYLE_ID,
            operation_id: 'op-1',
            operator_alias_id: 'alias-1',
            observed_seconds: '61.00',
            observed_at: '2026-09-20T10:00:00Z',
            is_outlier: true,
            outlier_approved_by: 'user-1',
            recorded_by: null,
            created_at: '2026-09-20T10:00:00Z',
          },
          { status: 200 },
        )
      }),
    )
    const { user } = renderRoute(`/f/F1/ie`)
    const lineSelect = await screen.findByLabelText('Line')
    await screen.findByRole('option', { name: 'Line 1 (L1)' })
    await user.selectOptions(lineSelect, LINE_ID)
    await screen.findByRole('option', { name: 'Crew tee (STY-1)' })
    await user.selectOptions(screen.getByLabelText('Style'), STYLE_ID)

    await user.selectOptions(await screen.findByLabelText(/^Operation/), 'op-1')
    await user.selectOptions(screen.getByLabelText(/Operator alias/), 'OP-A1')
    await user.type(screen.getByLabelText(/Observed cycle time/), '61')
    await user.type(screen.getByLabelText(/Observed at/), '2026-09-20T10:00')
    await user.click(screen.getByRole('button', { name: 'Record observation' }))

    await user.click(await screen.findByRole('button', { name: 'Mark as outlier' }))
    const reasonInput = screen.getByLabelText('Reason for marking this observation an outlier')
    const confirmButton = screen.getByRole('button', { name: 'Confirm' })

    // Blank: blocked, with an inline validation message.
    expect(confirmButton).toBeDisabled()
    expect(screen.getByText('Enter a reason for marking this observation an outlier.')).toBeInTheDocument()

    // Whitespace only: still blocked.
    await user.type(reasonInput, '   ')
    expect(confirmButton).toBeDisabled()
    expect(screen.getByText('Enter a reason for marking this observation an outlier.')).toBeInTheDocument()

    // A real reason (with surrounding whitespace) enables submit and is trimmed before sending.
    await user.type(reasonInput, ' Sewing machine jam ')
    expect(confirmButton).toBeEnabled()
    expect(screen.queryByText('Enter a reason for marking this observation an outlier.')).not.toBeInTheDocument()

    await user.click(confirmButton)

    expect(await screen.findByText('Marked as outlier')).toBeInTheDocument()
    expect(outlierBody).toEqual({ reason: 'Sewing machine jam' })
  })
})
