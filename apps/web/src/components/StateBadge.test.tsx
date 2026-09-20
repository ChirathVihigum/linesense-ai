import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StateBadge } from './StateBadge'
import { STATE_STYLES, type StateVocabulary } from './stateStyles'

const VOCABULARIES: Record<StateVocabulary, string[]> = {
  production: ['DRAFT', 'VALIDATED', 'PLANNED', 'IN_PRODUCTION', 'PRODUCTION_COMPLETE', 'DISPATCHED', 'CANCELLED'],
  material: ['UNKNOWN', 'READY', 'AT_RISK', 'SHORTAGE'],
  quality: ['NOT_INSPECTED', 'PENDING', 'HOLD', 'RELEASED'],
  analysis: ['QUEUED', 'RUNNING', 'AWAITING_REVIEW', 'COMPLETED', 'DEGRADED', 'FAILED', 'CANCELLED'],
  agent_result: ['SUCCEEDED', 'DEGRADED', 'FAILED'],
  recommendation: ['DRAFT', 'PROPOSED', 'APPROVED', 'REJECTED', 'EXPIRED', 'APPLIED', 'SUPERSEDED'],
  reservation: ['ACTIVE', 'RELEASED', 'CONSUMED'],
  policy: ['DRAFT', 'ACTIVE', 'RETIRED'],
  document_version: ['QUARANTINE', 'PROCESSING', 'ACTIVE', 'REJECTED', 'SUPERSEDED'],
  audit_outcome: ['SUCCESS', 'DENIED', 'FAILED'],
}

describe('StateBadge', () => {
  for (const [vocabulary, states] of Object.entries(VOCABULARIES) as [StateVocabulary, string[]][]) {
    it(`covers every ${vocabulary} state from the contract with its own icon and text`, () => {
      expect(Object.keys(STATE_STYLES[vocabulary]).sort()).toEqual([...states].sort())
      const icons = new Set<string>()
      for (const state of states) {
        const { container, unmount } = render(<StateBadge vocabulary={vocabulary} state={state} />)
        const badge = container.querySelector(`[data-state="${state}"]`)
        const expected = STATE_STYLES[vocabulary][state]
        expect(badge).not.toBeNull()
        expect(badge).toHaveTextContent(expected?.label ?? '')
        const icon = badge?.querySelector('svg[data-icon]')
        expect(icon).toHaveAttribute('aria-hidden', 'true')
        expect(icon?.getAttribute('data-icon')).toBe(expected?.icon)
        icons.add(icon?.getAttribute('data-icon') ?? '')
        unmount()
      }
      // Never colour alone: each state in a vocabulary has a distinct icon.
      expect(icons.size).toBe(states.length)
    })
  }

  it('names the vocabulary for screen readers', () => {
    render(<StateBadge vocabulary="material" state="AT_RISK" />)
    expect(screen.getByText('At risk').parentElement).toHaveTextContent('Materials: At risk')
  })

  it('renders an unknown state as neutral text with a question icon', () => {
    const { container } = render(<StateBadge vocabulary="quality" state="SOMETHING_NEW" />)
    expect(screen.getByText('Something new')).toBeInTheDocument()
    expect(container.querySelector('svg')).toHaveAttribute('data-icon', 'question')
  })
})
