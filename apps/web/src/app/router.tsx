import { Navigate, Outlet, type RouteObject } from 'react-router'

import { AuthProvider, useMe } from '../lib/auth'
import { FactoryProvider, pickDefaultFactory } from '../lib/factory'
import { LoginPage } from '../features/auth/LoginPage'
import { NoAccessPage } from '../features/auth/NoAccessPage'
import { Layout } from './Layout'
import { NotFoundPage } from './NotFoundPage'

/** Landing section inside a factory: the operations overview (Task 22). */
export const DEFAULT_FACTORY_SECTION = 'overview'

function HomeRedirect() {
  const me = useMe()
  const factory = pickDefaultFactory(me)
  if (!factory) return <Navigate to="/no-access" replace />
  return <Navigate to={`/f/${encodeURIComponent(factory.code)}/${DEFAULT_FACTORY_SECTION}`} replace />
}

function Authenticated() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  )
}

function FactoryScope() {
  return (
    <FactoryProvider>
      <Layout />
    </FactoryProvider>
  )
}

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <Authenticated />,
    children: [
      { index: true, element: <HomeRedirect /> },
      { path: '/no-access', element: <NoAccessPage /> },
      {
        path: '/f/:factoryCode',
        element: <FactoryScope />,
        children: [
          { index: true, element: <Navigate to={DEFAULT_FACTORY_SECTION} replace /> },
          // Feature screens are split into their own chunks and loaded on first visit.
          {
            path: 'overview',
            lazy: async () => ({
              Component: (await import('../features/overview/OverviewPage')).OverviewPage,
            }),
          },
          {
            path: 'orders',
            lazy: async () => ({ Component: (await import('../features/orders/OrdersPage')).OrdersPage }),
          },
          {
            path: 'orders/new',
            lazy: async () => ({
              Component: (await import('../features/orders/OrderCreatePage')).OrderCreatePage,
            }),
          },
          {
            path: 'orders/import',
            lazy: async () => ({
              Component: (await import('../features/orders/OrderImportPage')).OrderImportPage,
            }),
          },
          {
            path: 'orders/:orderId',
            lazy: async () => ({
              Component: (await import('../features/orders/OrderDetailPage')).OrderDetailPage,
            }),
          },
          {
            path: 'runs/:runId',
            lazy: async () => ({ Component: (await import('../features/runs/RunPage')).RunPage }),
          },
          {
            path: 'approvals',
            lazy: async () => ({
              Component: (await import('../features/approvals/ApprovalInboxPage')).ApprovalInboxPage,
            }),
          },
          {
            path: 'approvals/:recId',
            lazy: async () => ({
              Component: (await import('../features/approvals/RecommendationPage')).RecommendationPage,
            }),
          },
          {
            path: 'planning',
            lazy: async () => ({
              Component: (await import('../features/planning/PlanningBoardPage')).PlanningBoardPage,
            }),
          },
          {
            path: 'materials',
            lazy: async () => ({
              Component: (await import('../features/materials/MaterialsPage')).MaterialsPage,
            }),
          },
          {
            path: 'ie',
            lazy: async () => ({ Component: (await import('../features/ie/IEPage')).IEPage }),
          },
          {
            path: 'quality',
            lazy: async () => ({
              Component: (await import('../features/quality/QualityPage')).QualityPage,
            }),
          },
          {
            path: 'notes',
            lazy: async () => ({ Component: (await import('../features/notes/NotesPage')).NotesPage }),
          },
          {
            path: 'knowledge',
            lazy: async () => ({
              Component: (await import('../features/knowledge/KnowledgePage')).KnowledgePage,
            }),
          },
          {
            path: 'admin',
            lazy: async () => ({ Component: (await import('../features/admin/AdminPage')).AdminPage }),
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
  {
    path: '*',
    element: (
      <main id="main-content">
        <NotFoundPage />
      </main>
    ),
  },
]
