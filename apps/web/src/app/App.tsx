import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import { Providers, createQueryClient } from './providers'
import { routes } from './router'

const queryClient = createQueryClient()
const router = createBrowserRouter(routes)

export function App() {
  return (
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>
  )
}
