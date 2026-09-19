import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RecommendationCompare, type PlanningRecommendationSummary } from './RecommendationCompare'

function makeRecommendation(overrides: Partial<PlanningRecommendationSummary> = {}): PlanningRecommendationSummary {
  return {
    id: 'rec-1',
    orderId: 'order-1',
    orderExternalRef: 'PO-4001',
    allocatedUnits: 800,
    unscheduledUnits: 200,
    projectedFinishDate: '2026-10-20',
    linesUsed: ['L1', 'L2'],
    isStale: false,
    reviewPath: '/f/F1/planning/recommendations/rec-1',
    ...overrides,
  }
}

describe('RecommendationCompare', () => {
  it('shows an empty message when there is nothing to compare', () => {
    render(<RecommendationCompare recommendations={[]} />)
    expect(screen.getByText('No proposed recommendations to compare.')).toBeInTheDocument()
  })

  it('renders allocated units, unscheduled units, finish date and lines used', () => {
    render(<RecommendationCompare recommendations={[makeRecommendation()]} />)
    expect(screen.getByText('PO-4001')).toBeInTheDocument()
    expect(screen.getByText('800')).toBeInTheDocument()
    expect(screen.getByText('200')).toBeInTheDocument()
    expect(screen.getByText('20 Oct 2026')).toBeInTheDocument()
    expect(screen.getByText('L1, L2')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review recommendation' })).toHaveAttribute(
      'href',
      '/f/F1/planning/recommendations/rec-1',
    )
  })

  it('flags a stale recommendation with an icon and text', () => {
    render(<RecommendationCompare recommendations={[makeRecommendation({ isStale: true })]} />)
    expect(screen.getByText('Stale')).toBeInTheDocument()
  })
})
