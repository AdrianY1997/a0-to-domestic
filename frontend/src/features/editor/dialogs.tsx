import {
  Alert,
  Button,
  Group,
  Image,
  Loader,
  Modal,
  NumberInput,
  PasswordInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { postSheetPreview, setPhysicalSize, submitPassword } from '../../api/endpoints';
import type { JobSettings, PhysicalSizeBody } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { LengthInput } from '../../components/LengthInput';

// ---------------------------------------------------------------------------- password

export function PasswordDialog({
  documentId,
  invalid,
}: {
  documentId: string;
  /** The previous attempt was wrong (PASSWORD_INVALID). */
  invalid: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [password, setPassword] = useState('');
  const send = useMutation({
    mutationFn: () => submitPassword(documentId, password),
    onSuccess: (doc) => {
      setPassword('');
      qc.setQueryData(['document', documentId], doc);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password) send.mutate();
  };
  return (
    <Modal
      opened
      onClose={() => void navigate('/')}
      data-testid="password-dialog"
      title={t('password.title')}
      closeOnClickOutside={false}
      withCloseButton
      closeButtonProps={{ 'aria-label': t('common.close') }}
    >
      <form onSubmit={submit}>
        <Stack>
          <Text size="sm">{t('password.message')}</Text>
          {invalid && !send.isPending && (
            <Alert color="red" role="alert" data-testid="password-invalid">
              {t('errors.PASSWORD_INVALID')}
            </Alert>
          )}
          {send.isError && <ErrorAlert error={send.error} />}
          <PasswordInput
            data-autofocus
            data-testid="password-input"
            label={t('password.label')}
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => void navigate('/')}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" loading={send.isPending} disabled={!password}>
              {t('password.submit')}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------- physical size (ANA-03)

/** Source sizes the backend understands for `paper` (domain/papers.py SOURCE_SIZES). */
export const SOURCE_SIZE_IDS = [
  'A0',
  'A1',
  'A2',
  'A3',
  'A4',
  '2A0',
  '4A0',
  'B0',
  'B1',
  'B2',
  'ARCH_C',
  'ARCH_D',
  'ARCH_E',
  'ANSI_C',
  'ANSI_D',
  'ANSI_E',
] as const;

type SizeMode = 'paper' | 'width' | 'height' | 'dpi';

export function buildPhysicalSizeBody(
  mode: SizeMode,
  v: { paper: string; widthMm: number; heightMm: number; dpi: number },
): PhysicalSizeBody {
  switch (mode) {
    case 'paper':
      return { paper: v.paper };
    case 'width':
      return { width_mm: v.widthMm };
    case 'height':
      return { height_mm: v.heightMm };
    case 'dpi':
      return { dpi: v.dpi };
  }
}

export function PhysicalSizeDialog({
  documentId,
  pageIndex,
  opened,
  onClose,
}: {
  documentId: string;
  pageIndex: number;
  opened: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [mode, setMode] = useState<SizeMode>('paper');
  const [paper, setPaper] = useState('A0');
  const [widthMm, setWidthMm] = useState(841);
  const [heightMm, setHeightMm] = useState(1189);
  const [dpi, setDpi] = useState(72);
  const save = useMutation({
    mutationFn: () =>
      setPhysicalSize(
        documentId,
        pageIndex,
        buildPhysicalSizeBody(mode, { paper, widthMm, heightMm, dpi }),
      ),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['document', documentId] });
      await qc.invalidateQueries({ queryKey: ['plan'] });
      onClose();
    },
  });
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      data-testid="physical-size-dialog"
      title={t('physicalSize.title')}
      closeButtonProps={{ 'aria-label': t('common.close') }}
    >
      <Stack>
        <Text size="sm">{t('physicalSize.message')}</Text>
        <SegmentedControl
          fullWidth
          size="xs"
          value={mode}
          onChange={(v) => setMode(v as SizeMode)}
          aria-label={t('physicalSize.mode')}
          data={(['paper', 'width', 'height', 'dpi'] as const).map((m) => ({
            value: m,
            label: t(`physicalSize.modes.${m}`),
          }))}
        />
        {mode === 'paper' && (
          <Select
            label={t('physicalSize.paper')}
            allowDeselect={false}
            value={paper}
            onChange={(v) => v && setPaper(v)}
            data={SOURCE_SIZE_IDS.map((id) => ({ value: id, label: id.replace('_', ' ') }))}
          />
        )}
        {mode === 'width' && (
          <LengthInput
            label={t('physicalSize.width')}
            valueMm={widthMm}
            onChangeMm={setWidthMm}
            minMm={50}
          />
        )}
        {mode === 'height' && (
          <LengthInput
            label={t('physicalSize.height')}
            valueMm={heightMm}
            onChangeMm={setHeightMm}
            minMm={50}
          />
        )}
        {mode === 'dpi' && (
          <NumberInput
            label={t('physicalSize.dpi')}
            value={dpi}
            min={1}
            max={2400}
            onChange={(v) => typeof v === 'number' && setDpi(v)}
          />
        )}
        {save.isError && <ErrorAlert error={save.error} />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            {t('physicalSize.apply')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// ------------------------------------------------------------------ exact sheet preview

export function SheetPreviewModal({
  sheetId,
  settings,
  pageIndex,
  onClose,
}: {
  sheetId: string | null;
  settings: JobSettings;
  pageIndex: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['sheet-preview', sheetId, pageIndex, JSON.stringify(settings)],
    queryFn: () =>
      postSheetPreview({ settings, page_index: pageIndex, sheet_id: sheetId!, max_px: 1000 }),
    enabled: sheetId !== null,
    retry: false,
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data) {
      setUrl(null);
      return;
    }
    const u = URL.createObjectURL(query.data);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [query.data]);

  return (
    <Modal
      opened={sheetId !== null}
      onClose={onClose}
      size="xl"
      title={t('sheetPreview.title', { id: sheetId })}
      closeButtonProps={{ 'aria-label': t('common.close') }}
    >
      {query.isLoading && (
        <Group justify="center" p="xl">
          <Loader />
        </Group>
      )}
      {query.isError && <ErrorAlert error={query.error} />}
      {url && (
        <Image src={url} alt={t('sheetPreview.alt', { id: sheetId })} fit="contain" mah="75vh" />
      )}
    </Modal>
  );
}
