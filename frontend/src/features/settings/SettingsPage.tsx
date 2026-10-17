import {
  Alert,
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { purgeStorage } from '../../api/endpoints';
import { useInfo, usePrinters } from '../../api/queries';
import {
  SETTINGS_KEY,
  putAppSettings,
  useAppSettings,
  type AppSettingsInput,
} from '../../api/settings';
import { ErrorAlert } from '../../components/ErrorAlert';
import { LANGUAGES, type Language } from '../../i18n';
import type { DefaultPaper } from '../../lib/region';
import { LENGTH_UNITS, type LengthUnit } from '../../lib/units';
import { useAppStore } from '../../store/app';

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const unit = useAppStore((s) => s.unit);
  const setUnit = useAppStore((s) => s.setUnit);
  const info = useInfo();
  const purge = useMutation({
    mutationFn: purgeStorage,
    onSuccess: () => qc.invalidateQueries(),
  });

  const [confirming, setConfirming] = useState(false);
  const printers = usePrinters();
  const prefs = useAppSettings();
  const defaultPaper = useAppStore((s) => s.defaultPaper);
  const setDefaultPaper = useAppStore((s) => s.setDefaultPaper);
  const defaultPrinterId = useAppStore((s) => s.defaultPrinterId);
  const setDefaultPrinterId = useAppStore((s) => s.setDefaultPrinterId);
  const [retention, setRetention] = useState<number | string>('');
  const save = useMutation({
    mutationFn: (body: AppSettingsInput) => putAppSettings(body),
    onSuccess: (data) => qc.setQueryData(SETTINGS_KEY, data),
  });
  const retentionLocked = prefs.data?.retention_locked ?? true;
  const retentionValue = retention === '' ? (prefs.data?.retention_hours ?? '') : retention;
  const changeLanguage = (lang: Language) => {
    void i18n.changeLanguage(lang);
    save.mutate({ locale: lang });
  };

  return (
    <Stack maw={720} mx="auto" gap="lg">
      <Title order={1}>{t('settingsPage.title')}</Title>

      <Stack gap="xs" component="section">
        <Title order={2} size="h4" id="units-title">
          {t('settingsPage.units.title')}
        </Title>
        <SegmentedControl
          aria-labelledby="units-title"
          w="fit-content"
          value={unit}
          onChange={(v) => {
            setUnit(v as LengthUnit);
            save.mutate({ units: v as LengthUnit });
          }}
          data={LENGTH_UNITS.map((u) => ({ value: u, label: t(`settingsPage.units.${u}`) }))}
        />
        <Text size="xs" c="dimmed">
          {t('settingsPage.units.hint')}
        </Text>
      </Stack>

      <Stack gap="xs" component="section">
        <Title order={2} size="h4">
          {t('settingsPage.language.title')}
        </Title>
        <Select
          aria-label={t('settingsPage.language.title')}
          w={200}
          allowDeselect={false}
          value={i18n.resolvedLanguage ?? 'es'}
          data={LANGUAGES.map((l) => ({ value: l, label: t(`lang.${l}`) }))}
          onChange={(v) => v && changeLanguage(v as Language)}
        />
      </Stack>

      <Stack gap="xs" component="section">
        <Title order={2} size="h4">
          {t('settingsPage.defaults.title')}
        </Title>
        <Group align="flex-end">
          <Select
            label={t('settingsPage.defaults.paper')}
            w={200}
            allowDeselect={false}
            value={defaultPaper}
            data={[
              { value: 'A4', label: 'A4' },
              { value: 'LETTER', label: t('settingsPage.defaults.letter') },
            ]}
            onChange={(v) => {
              if (!v) return;
              setDefaultPaper(v as DefaultPaper);
              save.mutate({ default_paper: v as DefaultPaper });
            }}
          />
          <Select
            label={t('settingsPage.defaults.printer')}
            w={260}
            clearable
            placeholder={t('settingsPage.defaults.noPrinter')}
            value={defaultPrinterId}
            data={(printers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
            onChange={(v) => {
              setDefaultPrinterId(v);
              save.mutate({ default_printer_id: v });
            }}
          />
        </Group>
        {save.isError && <ErrorAlert error={save.error} />}
      </Stack>

      <Stack gap="xs" component="section">
        <Title order={2} size="h4">
          {t('settingsPage.storage.title')}
        </Title>
        <Text size="sm">
          {t('settingsPage.storage.text', { hours: info.data?.limits.retention_hours ?? '…' })}
        </Text>
        <Group align="flex-end">
          <NumberInput
            label={t('settingsPage.storage.retentionLabel')}
            w={160}
            min={1}
            max={8760}
            allowDecimal={false}
            value={retentionValue}
            disabled={retentionLocked}
            onChange={setRetention}
          />
          <Button
            variant="default"
            disabled={retentionLocked || typeof retention !== 'number'}
            loading={save.isPending}
            onClick={() =>
              typeof retention === 'number' && save.mutate({ retention_hours: retention })
            }
          >
            {t('settingsPage.storage.saveRetention')}
          </Button>
        </Group>
        {prefs.data?.retention_locked && (
          <Text size="xs" c="dimmed">
            {t('settingsPage.storage.retentionLocked')}
          </Text>
        )}
        <Group>
          <Button
            variant="default"
            loading={purge.isPending}
            onClick={() => purge.mutate('expired')}
          >
            {t('settingsPage.storage.purgeExpired')}
          </Button>
          <Button
            color="red"
            variant="light"
            loading={purge.isPending}
            onClick={() => setConfirming(true)}
          >
            {t('settingsPage.storage.purgeAll')}
          </Button>
        </Group>
        {confirming && (
          <Alert color="red" title={t('settingsPage.storage.confirmTitle')} role="alertdialog">
            <Text size="sm">{t('settingsPage.storage.confirmText')}</Text>
            <Group mt="xs">
              <Button
                color="red"
                size="xs"
                onClick={() => {
                  setConfirming(false);
                  purge.mutate('all');
                }}
              >
                {t('settingsPage.storage.purgeAll')}
              </Button>
              <Button variant="default" size="xs" onClick={() => setConfirming(false)}>
                {t('common.cancel')}
              </Button>
            </Group>
          </Alert>
        )}
        {purge.isSuccess && (
          <Alert color="green" role="status">
            {t('settingsPage.storage.done', {
              documents: purge.data.documents,
              jobs: purge.data.jobs,
            })}
          </Alert>
        )}
        {purge.isError && <ErrorAlert error={purge.error} />}
      </Stack>

      {info.data && (
        <Stack gap="xs" component="section">
          <Title order={2} size="h4">
            {t('settingsPage.system.title')}
          </Title>
          <Table withTableBorder>
            <Table.Tbody>
              <Table.Tr>
                <Table.Th scope="row">{t('settingsPage.system.version')}</Table.Th>
                <Table.Td>{info.data.version}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th scope="row">{t('settingsPage.system.maxUpload')}</Table.Th>
                <Table.Td>{info.data.limits.max_upload_mb} MB</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th scope="row">{t('settingsPage.system.maxSheets')}</Table.Th>
                <Table.Td>{info.data.limits.max_sheets}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Th scope="row">{t('settingsPage.system.retention')}</Table.Th>
                <Table.Td>{info.data.limits.retention_hours} h</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </Stack>
  );
}
