import { Alert, Button, Group, Progress, Stack, Text } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import { useBootStore } from '../store/boot';

/** Progress of the in-browser engine start (web version only, SPEC 17bis). */
export function BootStatus() {
  const { t } = useTranslation();
  const { phase, stage, pct, error } = useBootStore();
  if (phase === 'idle' || phase === 'ready') return null;

  if (phase === 'failed') {
    return (
      <Alert
        color="red"
        title={t('boot.failed.title')}
        role="alert"
        mb="md"
        data-testid="boot-failed"
      >
        <Stack gap="xs">
          <Text size="sm">{t('boot.failed.text')}</Text>
          {error && (
            <Text size="xs" c="dimmed">
              {error}
            </Text>
          )}
          <Group>
            <Button size="xs" onClick={() => window.location.reload()}>
              {t('boot.failed.retry')}
            </Button>
          </Group>
        </Stack>
      </Alert>
    );
  }

  return (
    <Alert
      color="blue"
      variant="light"
      mb="md"
      role="status"
      aria-live="polite"
      data-testid="boot-status"
    >
      <Stack gap={6}>
        <Text size="sm" fw={500}>
          {t('boot.title')}
        </Text>
        <Text size="xs">{t(`boot.stage.${stage}`, { defaultValue: t('boot.stage.runtime') })}</Text>
        <Progress value={pct} size="sm" animated aria-label={t('boot.title')} />
        <Text size="xs" c="dimmed">
          {t('boot.hint')}
        </Text>
      </Stack>
    </Alert>
  );
}
