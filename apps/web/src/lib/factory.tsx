import { createContext, use, useCallback, useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { useMe, type Me, type MeFactory } from './auth'
import { hasPermission, type Permission } from './permissions'

/** The only thing the app keeps in localStorage: the last selected factory code. */
export const LAST_FACTORY_STORAGE_KEY = 'linesense.lastFactoryCode'

export function readLastFactoryCode(): string | null {
  try {
    return window.localStorage.getItem(LAST_FACTORY_STORAGE_KEY)
  } catch {
    return null // storage unavailable (private mode, blocked site data)
  }
}

export function rememberFactoryCode(code: string): void {
  try {
    window.localStorage.setItem(LAST_FACTORY_STORAGE_KEY, code)
  } catch {
    // Remembering the factory is a convenience; the URL remains the source of truth.
  }
}

/** The factory to open by default: the remembered one if still permitted, else the first. */
export function pickDefaultFactory(me: Me): MeFactory | undefined {
  const remembered = readLastFactoryCode()
  return me.factories.find((f) => f.code === remembered) ?? me.factories[0]
}

const FactoryContext = createContext<MeFactory | null>(null)

/** Resolves `/f/:factoryCode/...` against the factories the user may access. */
export function FactoryProvider({ children }: { children: ReactNode }) {
  const { factoryCode } = useParams()
  const me = useMe()
  const factory = me.factories.find((f) => f.code === factoryCode)

  useEffect(() => {
    if (factory) rememberFactoryCode(factory.code)
  }, [factory])

  if (!factory) {
    return (
      <main id="main-content" className="mx-auto max-w-xl p-8">
        <h1 className="text-xl font-semibold tracking-tight">Factory not available</h1>
        <p className="mt-2 text-fg-muted">
          The factory &quot;{factoryCode}&quot; does not exist or you do not have access to it.
        </p>
        <Link className="link mt-4 inline-block" to="/">
          Go to your default factory
        </Link>
      </main>
    )
  }
  return <FactoryContext value={factory}>{children}</FactoryContext>
}

/** The factory selected by the URL segment `/f/:factoryCode/...`. */
export function useFactory(): MeFactory {
  const factory = use(FactoryContext)
  if (factory === null) throw new Error('useFactory() must be used inside <FactoryProvider>.')
  return factory
}

/** `can(permission)` for the selected factory. */
export function useCan(): (permission: Permission) => boolean {
  const factory = useFactory()
  return useCallback((permission: Permission) => hasPermission(factory, permission), [factory])
}
