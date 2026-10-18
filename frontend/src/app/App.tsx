import type { ReactNode } from 'react';
import { BrowserRouter, HashRouter } from 'react-router';
import { currentMode } from '../api/transport';
import { Providers } from './Providers';
import { AppRoutes } from './routes';

/**
 * The web version is a static site (GitHub Pages, no rewrites), so it routes with the URL hash and
 * deep links keep working. The Docker version uses real paths, under `BASE_URL` if it has one.
 */
export function AppRouter({ children }: { children: ReactNode }) {
  if (currentMode() === 'local') return <HashRouter>{children}</HashRouter>;
  const basename = import.meta.env.BASE_URL.replace(/\/+$/, '');
  return <BrowserRouter basename={basename || undefined}>{children}</BrowserRouter>;
}

export function App() {
  return (
    <Providers>
      <AppRouter>
        <AppRoutes />
      </AppRouter>
    </Providers>
  );
}
