import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse, delay } from 'msw'
import { describe, expect, it } from 'vitest'

import { renderRoute } from '../../test/render'
import { errorBody, server } from '../../test/server'

/** The dialog's own button, distinguished from the page's identically-named trigger. */
function lastOf<T>(items: T[]): T {
  const item = items.at(-1)
  if (item === undefined) throw new Error('Expected at least one matching element.')
  return item
}

function makeRecommendation(overrides: Record<string, unknown> = {}) {
  return {
    id: 'rec-1',
    order: {
      id: 'order-1',
      external_ref: 'PO-1001',
      production_state: 'VALIDATED',
      material_state: 'READY',
      quantity: 500,
      due_date: '2026-12-01',
      version: 2,
    },
    run: {
      id: 'run-1',
      status: 'AWAITING_REVIEW',
      llm: { provider: 'fixture', model: 'fixture-v1', is_fixture: true, label: 'Test fixture — not a live AI model' },
    },
    kind: 'ALLOCATION',
    status: 'PROPOSED',
    generated_by: 'deterministic',
    proposed_by_agent: 'planning',
    proposer: { id: 'proposer-1', display_name: 'Sam Supervisor' },
    rationale: 'Allocates the remaining units to line L1.',
    proposal: {},
    proposal_hash: 'hash-abc',
    input_versions: {},
    status_source: 'Calculated from records',
    decision: null,
    evidence: [],
    diff: { slots: [], reservations: [] },
    stale: false,
    stale_inputs: [],
    expired: false,
    can_decide: true,
    decide_blocked_reason: null,
    can_apply: false,
    apply_blocked_reason: 'WRONG_STATUS',
    expires_at: '2026-09-25T00:00:00Z',
    superseded_reason: null,
    applied_at: null,
    created_at: '2026-09-20T00:00:00Z',
    version: 1,
    ...overrides,
  }
}

function mockRecommendation(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get('/api/v1/recommendations/:recId', () => HttpResponse.json(makeRecommendation(overrides))),
  )
}

describe('RecommendationPage', () => {
  it('hides Approve/Reject for the proposer and explains why', async () => {
    mockRecommendation({ can_decide: false, decide_blocked_reason: 'SELF_APPROVAL' })
    renderRoute('/f/F1/approvals/rec-1')
    await screen.findByText('Recommendation for PO-1001')
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(
      screen.getByText('You proposed this analysis. Another supervisor must approve it.'),
    ).toBeInTheDocument()
  })

  it('requires a reason to reject', async () => {
    let decisionCalled = false
    mockRecommendation()
    server.use(
      http.post('/api/v1/recommendations/:recId/decision', () => {
        decisionCalled = true
        return HttpResponse.json({})
      }),
    )
    const { user } = renderRoute('/f/F1/approvals/rec-1')
    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    const rejectButtons = screen.getAllByRole('button', { name: 'Reject' })
    await user.click(lastOf(rejectButtons))
    expect(await screen.findByText(/Enter a reason of 3 to 500 characters/)).toBeInTheDocument()
    expect(decisionCalled).toBe(false)
  })

  it('sends the decision and reason once a valid reason is entered', async () => {
    let body: unknown
    mockRecommendation()
    server.use(
      http.post('/api/v1/recommendations/:recId/decision', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({
          id: 'rec-1',
          status: 'REJECTED',
          decision: 'REJECTED',
          decided_by: { id: 'u1', display_name: 'Sam Supervisor' },
          decided_at: '2026-09-20T05:00:00Z',
          reason: 'Not enough capacity.',
          version: 2,
        })
      }),
    )
    const { user } = renderRoute('/f/F1/approvals/rec-1')
    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    await user.type(screen.getByLabelText(/Reason/), 'Not enough capacity.')
    const rejectButtons = screen.getAllByRole('button', { name: 'Reject' })
    await user.click(lastOf(rejectButtons))
    await waitFor(() => {
      expect(body).toEqual({ decision: 'REJECTED', proposal_hash: 'hash-abc', reason: 'Not enough capacity.' })
    })
  })

  it('sends Idempotency-Key and proposal_hash on apply, and disables the button while pending', async () => {
    let idempotencyKey: string | null = null
    let body: unknown
    mockRecommendation({ status: 'APPROVED', can_apply: true, apply_blocked_reason: null })
    server.use(
      http.post('/api/v1/recommendations/:recId/apply', async ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        body = await request.json()
        // Never resolves: this test only needs the pending state, not the
        // success path (covered by the stale-inputs test's error path instead).
        await delay('infinite')
        return HttpResponse.json({})
      }),
    )
    const { user } = renderRoute('/f/F1/approvals/rec-1')
    await user.click(await screen.findByRole('button', { name: 'Apply' }))
    const confirmButtons = screen.getAllByRole('button', { name: 'Apply' })
    const confirmButton = lastOf(confirmButtons)
    await user.click(confirmButton)
    await waitFor(() => {
      expect(confirmButton).toBeDisabled()
    })
    await waitFor(() => {
      expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/)
    })
    expect(body).toEqual({ proposal_hash: 'hash-abc' })
  })

  it('renders the stale inputs list from a 409 STALE_INPUT apply response', async () => {
    mockRecommendation({ status: 'APPROVED', can_apply: true, apply_blocked_reason: null })
    server.use(
      http.post('/api/v1/recommendations/:recId/apply', () =>
        HttpResponse.json(
          errorBody('STALE_INPUT', 'The inputs this proposal was computed from have changed.', [
            {
              field: 'capacity_slot',
              message: 'slot-1 is at version 2; the proposal was computed against version 1.',
            },
          ]),
          { status: 409 },
        ),
      ),
    )
    const { user } = renderRoute('/f/F1/approvals/rec-1')
    await user.click(await screen.findByRole('button', { name: 'Apply' }))
    const confirmButtons = screen.getAllByRole('button', { name: 'Apply' })
    await user.click(lastOf(confirmButtons))
    expect(
      await screen.findByText('slot-1 is at version 2; the proposal was computed against version 1.'),
    ).toBeInTheDocument()
  })
})
