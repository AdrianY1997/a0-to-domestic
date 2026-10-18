import '@mantine/core/styles.css';
import '@mantine/dropzone/styles.css';
import '@mantine/notifications/styles.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { warmUpTransport } from './api/transport';
import { App } from './app/App';
import './i18n';

// Local mode starts booting Pyodide now, long before the first request needs it (SPEC 17bis).
warmUpTransport();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
