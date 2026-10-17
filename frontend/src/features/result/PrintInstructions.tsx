import { List, Modal, Stack, Text, Title } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import type { JoinMethod } from '../../api/types';

/** Print and assembly instructions (SPEC Appendix D). */
export function PrintInstructions({ paper, join }: { paper?: string; join?: JoinMethod }) {
  const { t } = useTranslation();
  const paperName = paper ?? t('instructions.paperFallback');
  return (
    <Stack gap="md">
      <section>
        <Title order={3} size="h5" mb="xs">
          {t('instructions.print.title')}
        </Title>
        <List type="ordered" spacing="xs" size="sm">
          <List.Item>{t('instructions.print.step1')}</List.Item>
          <List.Item>{t('instructions.print.step2', { paper: paperName })}</List.Item>
          <List.Item>{t('instructions.print.step3')}</List.Item>
        </List>
      </section>
      {join !== 'butt' && (
        <section>
          <Title order={3} size="h5" mb="xs">
            {t('instructions.overlap.title')}
          </Title>
          <List type="ordered" spacing="xs" size="sm">
            <List.Item>{t('instructions.overlap.step1')}</List.Item>
            <List.Item>{t('instructions.overlap.step2')}</List.Item>
            <List.Item>{t('instructions.overlap.step3')}</List.Item>
            <List.Item>{t('instructions.overlap.step4')}</List.Item>
          </List>
        </section>
      )}
      {join !== 'overlap' && (
        <section>
          <Title order={3} size="h5" mb="xs">
            {t('instructions.butt.title')}
          </Title>
          <Text size="sm">{t('instructions.butt.text')}</Text>
        </section>
      )}
    </Stack>
  );
}

export function PrintInstructionsModal({
  opened,
  onClose,
  paper,
  join,
}: {
  opened: boolean;
  onClose: () => void;
  paper?: string;
  join?: JoinMethod;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="lg"
      title={t('instructions.title')}
      closeButtonProps={{ 'aria-label': t('common.close') }}
    >
      <PrintInstructions paper={paper} join={join} />
    </Modal>
  );
}
