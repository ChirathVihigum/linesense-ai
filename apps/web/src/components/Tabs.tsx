import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

export interface TabItem {
  id: string
  label: string
  content: ReactNode
}

/** WAI-ARIA tabs with arrow-key, Home and End navigation. */
export function Tabs({ label, tabs }: { label: string; tabs: TabItem[] }) {
  const baseId = useId()
  const [activeId, setActiveId] = useState(tabs[0]?.id)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  const focusTab = (index: number) => {
    const tab = tabs[(index + tabs.length) % tabs.length]
    if (!tab) return
    setActiveId(tab.id)
    tabRefs.current[tab.id]?.focus()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: tabs.length - 1,
    }
    const target = moves[event.key]
    if (target === undefined) return
    event.preventDefault()
    focusTab(target)
  }

  return (
    <div>
      <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line">
        {tabs.map((tab, index) => {
          const selected = tab.id === activeId
          return (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[tab.id] = node
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${selected ? 'border-accent text-fg' : 'border-transparent text-fg-muted hover:text-fg'}`}
              onClick={() => {
                setActiveId(tab.id)
              }}
              onKeyDown={(event) => {
                onKeyDown(event, index)
              }}
            >
              {tab.label}
            </button>
          )
        })}
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${baseId}-panel-${tab.id}`}
          aria-labelledby={`${baseId}-tab-${tab.id}`}
          hidden={tab.id !== activeId}
          className="pt-4"
        >
          {tab.id === activeId && tab.content}
        </div>
      ))}
    </div>
  )
}
