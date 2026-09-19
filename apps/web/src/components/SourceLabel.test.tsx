import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SourceLabel, type Source } from './SourceLabel'

describe('SourceLabel', () => {
  const cases: [Source, string][] = [
    [{ kind: 'calculated' }, 'Calculated from records'],
    [{ kind: 'ai_recommendation' }, 'AI recommendation'],
    [{ kind: 'test_fixture' }, 'Test fixture — not a live AI model'],
    [{ kind: 'ai_unavailable' }, 'AI explanation unavailable'],
    [{ kind: 'pending_approval' }, 'Pending human approval'],
  ]

  it.each(cases)('renders %o as "%s" with an icon', (source, text) => {
    const { container } = render(<SourceLabel source={source} />)
    expect(screen.getByText(text)).toBeInTheDocument()
    expect(container.querySelector('svg[data-icon]')).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders the approver and the approval time in the factory time zone', () => {
    render(
      <SourceLabel
        source={{
          kind: 'approved',
          approvedBy: 'Sam Supervisor',
          approvedAt: '2026-09-18T04:30:00Z',
          timeZone: 'Asia/Colombo',
        }}
      />,
    )
    // 04:30 UTC is 10:00 in Asia/Colombo (UTC+05:30).
    expect(screen.getByText(/^Approved by Sam Supervisor at 18 Sept 2026, 10:00/)).toBeInTheDocument()
  })
})
