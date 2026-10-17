import { Stack, Text, Title } from '@mantine/core';
import { useTranslation } from 'react-i18next';

interface Props {
  name: string;
  params?: Record<string, string | undefined>;
}

export function PlaceholderPage({ name, params }: Props) {
  const { t } = useTranslation();
  return (
    <Stack gap="xs">
      <Title order={1}>{t(`pages.${name}.title`)}</Title>
      <Text>{t(`pages.${name}.description`, params)}</Text>
      <Text c="dimmed">{t('common.placeholder')}</Text>
    </Stack>
  );
}
