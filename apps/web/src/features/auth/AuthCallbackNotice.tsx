import { Icon } from '../../components/Icon'

const MESSAGES: Record<string, string> = {
  auth_failed:
    'Sign-in did not complete. Please try again; if it keeps failing, contact your administrator.',
}

/** Explains why the sign-in callback sent the user back to /login (`?error=...`). */
export function AuthCallbackNotice({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div role="alert" className="flex gap-2 rounded-md border border-bad-line bg-bad-bg p-3 text-bad-fg">
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
      <p>{MESSAGES[error] ?? 'Sign-in failed. Please try again.'}</p>
    </div>
  )
}
