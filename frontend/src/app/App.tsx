import { BrowserRouter } from 'react-router';
import { Providers } from './Providers';
import { AppRoutes } from './routes';

export function App() {
  return (
    <Providers>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </Providers>
  );
}
