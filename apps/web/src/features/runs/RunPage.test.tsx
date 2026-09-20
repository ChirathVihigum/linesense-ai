import { act, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderRoute } from '../../test/render'
import { server } from '../../test/server'

function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: 'run-1',
    order: { id: 'order-1', external_ref: 'PO-1001' },
    status: 'RUNNING',
    requested_by: { id: 'u1', display_name: 'Pat Planner' },
    llm: { provider: 'fixture', model: 'fixture-v1', is_fixture: true, label: 'Test fixture — not a live AI model' },
    degraded_reason: null,
    error_code: null,
    created_at: '2026-09-20T04:00:00Z',
    completed_at: null,
    model_calls_used: 2,
    model_calls_limit: 12,
    tokens_used: 500,
    replan_count: 0,
    started_at: '2026-09-20T04:00:01Z',
    deadline_at: '2026-09-20T04:02:00Z',
    tasks: [],
    results: [],
    recommendations: [],
    report: null,
    ...overrides,
  }
}

describe('RunPage', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows the fixture label prominently', async () => {
    server.use(
      http.get('/api/v1/runs/:runId', () =>
        HttpResponse.json(makeRun({ status: 'COMPLETED', completed_at: '2026-09-20T04:02:00Z' })),
      ),
      http.get('/api/v1/runs/:runId/events', () => HttpResponse.json([])),
    )
    renderRoute('/f/F1/runs/run-1')
    expect(await screen.findByText('Test fixture — not a live AI model')).toBeInTheDocument()
  })

  it('polls every 2 seconds while RUNNING and stops once the run is COMPLETED', async () => {
    let calls = 0
    server.use(
      http.get('/api/v1/runs/:runId', () => {
        calls += 1
        const status = calls < 3 ? 'RUNNING' : 'COMPLETED'
        return HttpResponse.json(
          makeRun({ status, completed_at: status === 'COMPLETED' ? '2026-09-20T04:02:00Z' : null }),
        )
      }),
      http.get('/api/v1/runs/:runId/events', () => HttpResponse.json([])),
    )
    renderRoute('/f/F1/runs/run-1')
    await screen.findByText('Analysis run for PO-1001')
    await waitFor(() => {
      expect(calls).toBe(1)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    await waitFor(() => {
      expect(calls).toBe(2)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    await waitFor(() => {
      expect(calls).toBe(3)
    })

    const callsOnceCompleted = calls
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(calls).toBe(callsOnceCompleted)
  })
})
