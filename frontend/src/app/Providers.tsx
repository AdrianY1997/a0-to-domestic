import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';
import { theme } from './theme';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export function Providers({ children }: { children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <MantineProvider theme={theme} defaultColorScheme="auto">
          <Notifications />
          {children}
        </MantineProvider>
      </QueryClientProvider>
    </I18nextProvider>
  );
}
