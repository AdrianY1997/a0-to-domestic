import { Accordion, Badge, Group, List, Stack, Text, Title } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import { useInfo } from '../../api/queries';
import { PrintInstructions } from '../result/PrintInstructions';

const VIEWERS = ['chrome', 'edge', 'firefox', 'acrobat', 'preview'] as const;

export default function HelpPage() {
  const { t } = useTranslation();
  const info = useInfo();
  const formats = [
    ...new Set((info.data?.converters ?? []).filter((c) => c.available).flatMap((c) => c.formats)),
  ].sort();

  return (
    <Stack maw={860} mx="auto" gap="lg">
      <Title order={1}>{t('help.title')}</Title>
      <Text c="dimmed">{t('help.intro')}</Text>

      <section>
        <Title order={2} size="h3" mb="sm">
          {t('help.print100.title')}
        </Title>
        <Text size="sm" mb="sm">
          {t('help.print100.intro')}
        </Text>
        <Accordion variant="separated" multiple defaultValue={['chrome']}>
          {VIEWERS.map((v) => (
            <Accordion.Item key={v} value={v}>
              <Accordion.Control>{t(`help.viewers.${v}.name`)}</Accordion.Control>
              <Accordion.Panel>
                <Text size="sm">{t(`help.viewers.${v}.text`)}</Text>
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      </section>

      <section>
        <Title order={2} size="h3" mb="sm">
          {t('help.assembly.title')}
        </Title>
        <PrintInstructions />
      </section>

      <section>
        <Title order={2} size="h3" mb="sm">
          {t('help.formats.title')}
        </Title>
        <Group gap={6}>
          {formats.map((f) => (
            <Badge key={f} variant="light" color="gray">
              {f.toUpperCase()}
            </Badge>
          ))}
        </Group>
      </section>

      <section>
        <Title order={2} size="h3" mb="sm">
          {t('help.faq.title')}
        </Title>
        <List spacing="sm" size="sm" listStyleType="none">
          {(['scale', 'blank', 'raster'] as const).map((q) => (
            <List.Item key={q}>
              <Text fw={600}>{t(`help.faq.${q}.q`)}</Text>
              <Text>{t(`help.faq.${q}.a`)}</Text>
            </List.Item>
          ))}
        </List>
      </section>
    </Stack>
  );
}
