import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, use, type ReactNode } from 'react'

import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { ApiError, api, browserNavigation, setCsrfToken, unwrap, type Schemas } from './api'

export type Me = Schemas['MeResponse']
export type MeFactory = Schemas['MeFactory']

export const ME_QUERY_KEY = ['me'] as const

async function fetchMe(): Promise<Me> {
  const me = await unwrap(api.GET('/api/v1/me'))
  // Held in memory only (never localStorage); the API middleware reads it.
  setCsrfToken(me.csrf_token)
  return me
}

const AuthContext = createContext<Me | null>(null)

/** Loads `GET /api/v1/me` and provides it to the authenticated part of the app. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const query = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: fetchMe,
    staleTime: 5 * 60_000,
    retry: false,
  })

  if (query.isPending) return <LoadingState label="Loading your session…" variant="page" />
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 401) {
      // The API middleware is already sending the browser to /login.
      return <LoadingState label="Redirecting to sign in…" variant="page" />
    }
    return (
      <main className="mx-auto max-w-xl p-8">
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </main>
    )
  }
  return <AuthContext value={query.data}>{children}</AuthContext>
}

export function useMe(): Me {
  const me = use(AuthContext)
  if (me === null) throw new Error('useMe() must be used inside <AuthProvider>.')
  return me
}

/** `POST /auth/logout` (CSRF header added by the API middleware), then back to /login. */
export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/auth/logout')),
    onSuccess: () => {
      setCsrfToken(null)
      queryClient.clear()
      browserNavigation.assign('/login')
    },
  })
}
