import { Alert, Button, Group, Modal, Stack, TextInput } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import type { JobSettings } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { createPreset, settingsToPreset } from './api';

/** "Save as preset" on the result page: stores the settings the job was generated with. */
export function SaveAsPreset({ settings }: { settings: JobSettings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [name, setName] = useState('');
  const save = useMutation({
    mutationFn: () => createPreset({ name: name.trim(), settings: settingsToPreset(settings) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['presets'] }),
  });

  return (
    <>
      <Button variant="default" onClick={() => setOpened(true)}>
        {t('presetsPage.saveAs')}
      </Button>
      <Modal
        opened={opened}
        onClose={() => {
          setOpened(false);
          save.reset();
        }}
        title={t('presetsPage.saveAs')}
      >
        <Stack>
          {save.isSuccess ? (
            <Alert color="green" role="status">
              {t('presetsPage.saved')} <Link to="/presets">{t('presetsPage.goToPresets')}</Link>
            </Alert>
          ) : (
            <>
              <TextInput
                label={t('presetsPage.nameLabel')}
                value={name}
                maxLength={120}
                data-autofocus
                onChange={(e) => setName(e.currentTarget.value)}
              />
              {save.isError && <ErrorAlert error={save.error} />}
              <Group justify="flex-end">
                <Button
                  disabled={!name.trim()}
                  loading={save.isPending}
                  onClick={() => save.mutate()}
                >
                  {t('presetsPage.save')}
                </Button>
              </Group>
            </>
          )}
        </Stack>
      </Modal>
    </>
  );
}
