import {
  ActionIcon,
  AppShell,
  Anchor,
  Button,
  Center,
  Group,
  Loader,
  Select,
  Stack,
  Text,
  Title,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { SESSION_KEY, logout, useAuthRequiredWatcher, useSession } from '../api/auth';
import { useSyncPreferences } from '../api/settings';
import { BootStatus } from '../components/BootStatus';
import { LANGUAGES } from '../i18n';

const NAV = [
  { to: '/', key: 'nav.home', end: true },
  { to: '/printers', key: 'nav.printers', end: false },
  { to: '/presets', key: 'nav.presets', end: false },
  { to: '/history', key: 'nav.history', end: false },
  { to: '/help', key: 'nav.help', end: false },
  { to: '/settings', key: 'nav.settings', end: false },
] as const;

export function Layout() {
  const { t, i18n } = useTranslation();
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme('light', { getInitialValueInEffect: true });
  const qc = useQueryClient();
  const location = useLocation();
  useAuthRequiredWatcher();
  const session = useSession();
  const passwordMode = session.data?.auth_mode === 'password';
  const tokenMode = session.data?.auth_mode === 'token';
  const loggedOut = passwordMode && !session.data?.authenticated;
  useSyncPreferences(!!session.data && !loggedOut && !tokenMode);

  if (tokenMode) {
    // Token mode is for API/CLI clients: the browser cannot send the Bearer header on SSE/downloads.
    return (
      <Center mih="100vh" p="xl" data-testid="token-mode-notice">
        <Stack maw={520} gap="sm">
          <Title order={2}>{t('tokenMode.title')}</Title>
          <Text>{t('tokenMode.body')}</Text>
          <Text c="dimmed" fz="sm">
            {t('tokenMode.hint')}
          </Text>
        </Stack>
      </Center>
    );
  }

  if (loggedOut && location.pathname !== '/login') return <Navigate to="/login" replace />;

  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="lg" wrap="nowrap">
            <Text fw={700}>{t('app.name')}</Text>
            <Group component="nav" aria-label={t('nav.main')} gap="md" wrap="nowrap">
              {NAV.map((item) => (
                <Anchor
                  key={item.to}
                  component={NavLink}
                  to={item.to}
                  end={item.end}
                  underline="never"
                  fz="sm"
                >
                  {t(item.key)}
                </Anchor>
              ))}
            </Group>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <Select
              aria-label={t('header.language')}
              w={120}
              size="xs"
              allowDeselect={false}
              data={LANGUAGES.map((l) => ({ value: l, label: t(`lang.${l}`) }))}
              value={i18n.resolvedLanguage ?? 'es'}
              onChange={(v) => v && void i18n.changeLanguage(v)}
            />
            {passwordMode && session.data?.authenticated && (
              <Button
                size="xs"
                variant="default"
                onClick={() =>
                  void logout().then(() => {
                    qc.clear();
                    qc.setQueryData(SESSION_KEY, { ...session.data, authenticated: false });
                  })
                }
              >
                {t('header.logout')}
              </Button>
            )}
            <ActionIcon
              variant="default"
              size="lg"
              aria-label={t('header.toggleTheme')}
              onClick={() => setColorScheme(computed === 'dark' ? 'light' : 'dark')}
            >
              {computed === 'dark' ? '☀' : '☾'}
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <BootStatus />
        <Suspense
          fallback={
            <Center p="xl">
              <Loader size="sm" />
            </Center>
          }
        >
          <Outlet />
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
