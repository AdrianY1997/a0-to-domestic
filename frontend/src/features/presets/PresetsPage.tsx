import {
  Badge,
  Button,
  FileButton,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePresets } from '../../api/queries';
import type { Preset } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { toJobSettings } from '../../lib/settings';
import { useEditorStore } from '../../store/editor';
import {
  createPreset,
  deletePreset,
  importPreset,
  presetExportUrl,
  settingsToPreset,
  updatePreset,
} from './api';

export default function PresetsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const presets = usePresets();
  const lastDocId = useEditorStore((s) => s.order[s.order.length - 1] ?? null);
  const lastSettings = useEditorStore((s) => (lastDocId ? s.docs[lastDocId]?.settings : undefined));

  const [newName, setNewName] = useState('');
  const [renaming, setRenaming] = useState<Preset | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ['presets'] });
  const create = useMutation({
    mutationFn: createPreset,
    onSuccess: () => {
      setNewName('');
      return refresh();
    },
  });
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updatePreset(id, { name }),
    onSuccess: () => {
      setRenaming(null);
      return refresh();
    },
  });
  const remove = useMutation({
    mutationFn: deletePreset,
    onSuccess: () => {
      setConfirmId(null);
      return refresh();
    },
  });
  const imp = useMutation({ mutationFn: importPreset, onSuccess: refresh });

  const onFile = async (file: File | null) => {
    if (!file) return;
    setImportError(null);
    try {
      imp.mutate(JSON.parse(await file.text()) as unknown);
    } catch {
      setImportError(t('presetsPage.importInvalid'));
    }
  };

  const canCreate = !!lastDocId && !!lastSettings && newName.trim().length > 0;
  const saveCurrent = () => {
    if (!lastDocId || !lastSettings) return;
    create.mutate({
      name: newName.trim(),
      settings: settingsToPreset(toJobSettings(lastDocId, lastSettings)),
    });
  };

  const error = presets.error ?? create.error ?? rename.error ?? remove.error ?? imp.error;

  return (
    <Stack maw={960} mx="auto" gap="lg">
      <Title order={1}>{t('presetsPage.title')}</Title>
      <Text size="sm" c="dimmed">
        {t('presetsPage.intro')}
      </Text>

      <Stack gap="xs" component="section" aria-label={t('presetsPage.createTitle')}>
        <Title order={2} size="h4">
          {t('presetsPage.createTitle')}
        </Title>
        <Group align="flex-end">
          <TextInput
            label={t('presetsPage.nameLabel')}
            value={newName}
            maxLength={120}
            onChange={(e) => setNewName(e.currentTarget.value)}
            disabled={!lastDocId}
          />
          <Button onClick={saveCurrent} disabled={!canCreate} loading={create.isPending}>
            {t('presetsPage.createFromCurrent')}
          </Button>
          <FileButton onChange={(f) => void onFile(f)} accept="application/json,.json">
            {(props) => (
              <Button variant="default" loading={imp.isPending} {...props}>
                {t('presetsPage.import')}
              </Button>
            )}
          </FileButton>
        </Group>
        {!lastDocId && (
          <Text size="xs" c="dimmed">
            {t('presetsPage.noDocument')}
          </Text>
        )}
        {importError && (
          <Text size="sm" c="red" role="alert">
            {importError}
          </Text>
        )}
      </Stack>

      {error && <ErrorAlert error={error} />}

      <Table withTableBorder aria-label={t('presetsPage.title')}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t('presetsPage.colName')}</Table.Th>
            <Table.Th>{t('presetsPage.colType')}</Table.Th>
            <Table.Th>{t('presetsPage.colActions')}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(presets.data ?? []).map((p) => (
            <Table.Tr key={p.id}>
              <Table.Td>{p.name}</Table.Td>
              <Table.Td>
                <Badge variant="light" color={p.builtin ? 'gray' : 'blue'}>
                  {p.builtin ? t('presetsPage.builtin') : t('presetsPage.user')}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Group gap="xs">
                  <Button
                    component="a"
                    href={presetExportUrl(p.id)}
                    download
                    size="xs"
                    variant="default"
                  >
                    {t('presetsPage.export')}
                  </Button>
                  {!p.builtin && (
                    <>
                      <Button
                        size="xs"
                        variant="default"
                        onClick={() => {
                          setRenaming(p);
                          setRenameValue(p.name);
                        }}
                      >
                        {t('presetsPage.rename')}
                      </Button>
                      {confirmId === p.id ? (
                        <>
                          <Button
                            size="xs"
                            color="red"
                            loading={remove.isPending}
                            onClick={() => remove.mutate(p.id)}
                          >
                            {t('presetsPage.confirmDelete')}
                          </Button>
                          <Button size="xs" variant="default" onClick={() => setConfirmId(null)}>
                            {t('common.cancel')}
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="xs"
                          color="red"
                          variant="light"
                          onClick={() => setConfirmId(p.id)}
                        >
                          {t('presetsPage.delete')}
                        </Button>
                      )}
                    </>
                  )}
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Modal opened={!!renaming} onClose={() => setRenaming(null)} title={t('presetsPage.rename')}>
        <Stack>
          <TextInput
            label={t('presetsPage.nameLabel')}
            value={renameValue}
            maxLength={120}
            data-autofocus
            onChange={(e) => setRenameValue(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRenaming(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={!renameValue.trim()}
              loading={rename.isPending}
              onClick={() => renaming && rename.mutate({ id: renaming.id, name: renameValue })}
            >
              {t('presetsPage.save')}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
