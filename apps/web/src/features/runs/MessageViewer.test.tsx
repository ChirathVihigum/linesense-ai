import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MessageViewer } from './MessageViewer'

describe('MessageViewer', () => {
  it('shows the recipient and task type from the envelope', () => {
    render(
      <MessageViewer
        envelope={{
          sender: 'orchestrator',
          recipient: 'rm',
          task_type: 'assess_material_readiness',
          round: 0,
          input_refs: [{ type: 'snapshot', id: 'snap-1', version: null }],
          constraints: { max_tool_calls: 4, read_only: true },
        }}
      />,
    )
    expect(screen.getByText('rm')).toBeInTheDocument()
    // Matches the visible summary line specifically (not the JSON inside the
    // collapsed <details>, which also contains this task type as a substring).
    expect(screen.getByText(/· assess_material_readiness/)).toBeInTheDocument()
  })

  it('keeps the full envelope collapsible, pretty-printed JSON', () => {
    render(<MessageViewer envelope={{ sender: 'orchestrator', recipient: 'ie', task_type: 'assess_line_capability' }} />)
    const details = screen.getByText('View envelope').closest('details')
    expect(details).not.toBeNull()
    expect(screen.getByText(/"recipient": "ie"/)).toBeInTheDocument()
  })
})
