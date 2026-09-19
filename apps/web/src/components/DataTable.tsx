import type { ReactNode } from 'react'

export interface Column<Row> {
  key: string
  header: string
  render: (row: Row) => ReactNode
  align?: 'left' | 'right'
}

export interface SortState {
  key: string
  direction: 'ascending' | 'descending'
}

const SORT_ARROWS: Record<SortState['direction'], string> = { ascending: '▲', descending: '▼' }

/**
 * A plain accessible table. `sort` marks the column the server orders by
 * (`aria-sort` + a visible arrow and text); the table never re-sorts locally.
 */
export function DataTable<Row>({
  caption,
  columns,
  rows,
  rowKey,
  sort,
}: {
  caption: string
  columns: Column<Row>[]
  rows: Row[]
  rowKey: (row: Row) => string
  sort?: SortState
}) {
  return (
    <div className="panel overflow-x-auto">
      <table className="min-w-full divide-y divide-line text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-sunken">
          <tr>
            {columns.map((column) => {
              const sorted = sort?.key === column.key ? sort.direction : undefined
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={sorted}
                  className={`px-3 py-2 text-xs font-medium whitespace-nowrap text-fg-muted ${column.align === 'right' ? 'text-right' : 'text-left'}`}
                >
                  {column.header}
                  {sorted && (
                    <span className="ml-1 text-fg">
                      <span aria-hidden="true">{SORT_ARROWS[sorted]}</span>
                      <span className="sr-only"> (sorted {sorted})</span>
                    </span>
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row) => (
            <tr key={rowKey(row)} className="hover:bg-surface-sunken/60">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-3 py-2 align-top ${column.align === 'right' ? 'text-right tabular-nums' : ''}`}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
