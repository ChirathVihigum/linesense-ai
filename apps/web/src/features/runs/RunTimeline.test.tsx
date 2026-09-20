import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Schemas } from '../../lib/api'
import { RunTimeline } from './RunTimeline'

function event(overrides: Partial<Schemas['RunEventOut']>): Schemas['RunEventOut'] {
  return {
    id: 1,
    event_type: 'run.created',
    actor: 'user:1',
    payload: {},
    created_at: '2026-09-20T04:00:00Z',
    ...overrides,
  }
}

describe('RunTimeline', () => {
  it('shows an empty message with no events', () => {
    render(<RunTimeline events={[]} timeZone="UTC" />)
    expect(screen.getByText('No events yet.')).toBeInTheDocument()
  })

  it('highlights an orchestrator.replan event with its reason', () => {
    render(
      <RunTimeline
        events={[
          event({ id: 2, event_type: 'orchestrator.replan', actor: 'orchestrator', payload: { reason: 'MATERIAL_SHORTAGE_CONFLICT' } }),
        ]}
        timeZone="UTC"
      />,
    )
    expect(screen.getByText('Replanning')).toBeInTheDocument()
    expect(screen.getByText(/MATERIAL_SHORTAGE_CONFLICT/)).toBeInTheDocument()
  })

  it('shows a dispatched task envelope via MessageViewer', () => {
    render(
      <RunTimeline
        events={[
          event({
            id: 3,
            event_type: 'task.dispatched',
            actor: 'system',
            payload: {
              task_id: 't1',
              recipient: 'planning',
              task_type: 'propose_allocation',
              round: 0,
              message: { sender: 'orchestrator', recipient: 'planning', task_type: 'propose_allocation', round: 0 },
            },
          }),
        ]}
        timeZone="UTC"
      />,
    )
    expect(screen.getByText('planning')).toBeInTheDocument()
    expect(screen.getByText(/· propose_allocation/)).toBeInTheDocument()
  })
})
