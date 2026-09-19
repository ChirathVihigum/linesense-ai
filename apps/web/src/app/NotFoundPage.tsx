import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="text-xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-fg-muted">There is no page at this address.</p>
      <Link className="link mt-4 inline-block" to="/">
        Go to the start page
      </Link>
    </div>
  )
}
