import {
  ActionIcon,
  Alert,
  Button,
  Group,
  MultiSelect,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { usePapers, usePrinters, useInfo } from '../../api/queries';
import { ippCapabilities } from '../../api/endpoints';
import type { IppCapabilities, Margins, PrinterProfile } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { createPrinter, updatePrinter, type PrinterInput } from './api';
import { prefillFromCapabilities } from './ipp';
import { MarginsFields } from './MarginsFields';

const KINDS: PrinterProfile['kind'][] = ['generic', 'inkjet', 'laser', 'borderless'];
const DEFAULT_MARGINS: Margins = { top: 5, right: 5, bottom: 5, left: 5 };

interface FormState {
  name: string;
  kind: PrinterProfile['kind'];
  color: boolean;
  borderless: boolean;
  /** `null` = every catalog paper. */
  supported: string[] | null;
  margins: Margins;
  perPaper: Record<string, Margins>;
  ippUri: string;
}

function fromProfile(p: PrinterProfile | undefined, name: string): FormState {
  return {
    name,
    kind: p?.kind ?? 'generic',
    color: p?.color ?? true,
    borderless: p?.borderless ?? false,
    supported: p?.supported_papers ? [...p.supported_papers] : null,
    margins: { ...DEFAULT_MARGINS, ...p?.default_margins },
    perPaper: { ...p?.paper_margins },
    ippUri: p?.ipp_uri ?? '',
  };
}

export default function PrinterFormPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const sourceId = id ?? search.get('from') ?? undefined;
  const printers = usePrinters();
  const source = printers.data?.find((p) => p.id === sourceId);
  if (sourceId && printers.isPending) return null;
  // Keyed so the form state is built once the source profile is known.
  return (
    <PrinterForm key={source?.id ?? 'new'} source={source} editing={!!source && !source.builtin} />
  );
}

function PrinterForm({
  source,
  editing,
}: {
  source: PrinterProfile | undefined;
  editing: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const papers = usePapers();
  const info = useInfo();
  const [caps, setCaps] = useState<IppCapabilities | null>(null);
  const [state, setState] = useState<FormState>(() =>
    fromProfile(
      source,
      source && !editing ? `${source.name} ${t('printers.copySuffix')}` : (source?.name ?? ''),
    ),
  );
  const patch = (p: Partial<FormState>) => setState((s) => ({ ...s, ...p }));

  const paperOptions = useMemo(
    () => (papers.data ?? []).map((p) => ({ value: p.id, label: p.name })),
    [papers.data],
  );
  const allIds = paperOptions.map((o) => o.value);
  const supported = state.supported ?? allIds;
  const addable = paperOptions.filter(
    (o) => supported.includes(o.value) && !(o.value in state.perPaper),
  );

  const readCaps = useMutation({
    mutationFn: () => ippCapabilities(state.ippUri.trim()),
    onSuccess: (data) => {
      setCaps(data);
      const fill = prefillFromCapabilities(data, { name: state.name, margins: state.margins });
      setState((s) => ({
        ...s,
        name: fill.name,
        color: fill.color,
        supported: fill.supported ?? s.supported,
        margins: fill.margins,
        perPaper: fill.supported ? fill.perPaper : s.perPaper,
      }));
    },
  });

  const save = useMutation({
    mutationFn: () => {
      const body: PrinterInput = {
        name: state.name.trim(),
        kind: state.kind,
        default_margins: state.margins,
        paper_margins: state.perPaper,
        borderless: state.borderless,
        color: state.color,
        scale_kx: editing ? (source?.scale_kx ?? 1) : 1,
        scale_ky: editing ? (source?.scale_ky ?? 1) : 1,
        ...(state.supported ? { supported_papers: state.supported } : {}),
        // Keep what the form does not edit (defaults incl. reserved keys, IPP URI) on update.
        ...(source?.defaults ? { defaults: source.defaults } : {}),
        ipp_uri: state.ippUri.trim() || null,
      };
      return editing && source ? updatePrinter(source.id, body) : createPrinter(body);
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['printers'] });
      void navigate('/printers');
    },
  });

  return (
    <Stack
      maw={720}
      mx="auto"
      gap="md"
      component="form"
      onSubmit={(e: React.FormEvent) => {
        e.preventDefault();
        if (state.name.trim()) save.mutate();
      }}
    >
      <Title order={1}>
        {editing ? t('printers.form.titleEdit') : t('printers.form.titleNew')}
      </Title>
      {source?.builtin && <Alert color="blue">{t('printers.form.readonly')}</Alert>}

      <TextInput
        label={t('printers.form.name')}
        required
        maxLength={120}
        value={state.name}
        onChange={(e) => patch({ name: e.currentTarget.value })}
      />
      <Select
        label={t('printers.form.kind')}
        allowDeselect={false}
        value={state.kind}
        data={KINDS.map((k) => ({ value: k, label: t(`printers.kind.${k}`) }))}
        onChange={(v) => v && patch({ kind: v as PrinterProfile['kind'] })}
      />
      <Group>
        <Switch
          label={t('printers.form.color')}
          checked={state.color}
          onChange={(e) => patch({ color: e.currentTarget.checked })}
        />
        <Switch
          label={t('printers.form.borderless')}
          checked={state.borderless}
          onChange={(e) => patch({ borderless: e.currentTarget.checked })}
        />
      </Group>
      <MultiSelect
        label={t('printers.form.papers')}
        searchable
        data={paperOptions}
        value={supported}
        onChange={(v) => patch({ supported: v.length === allIds.length ? null : v })}
      />

      {info.data?.ipp_enabled && (
        <Stack gap="xs">
          <Group align="flex-end" wrap="nowrap">
            <TextInput
              style={{ flex: 1 }}
              label={t('printers.form.ippUri')}
              description={t('printers.form.ippUriHint')}
              placeholder="ipp://192.168.1.20:631/ipp/print"
              value={state.ippUri}
              onChange={(e) => patch({ ippUri: e.currentTarget.value })}
            />
            <Button
              variant="light"
              onClick={() => readCaps.mutate()}
              loading={readCaps.isPending}
              disabled={!state.ippUri.trim()}
            >
              {t('printers.form.readCaps')}
            </Button>
          </Group>
          {readCaps.isError && <ErrorAlert error={readCaps.error} />}
          {caps && (
            <Alert color="green" role="status" data-testid="ipp-caps-result">
              {t('printers.form.capsRead', {
                count: caps.papers.length,
                pdf: caps.accepts_pdf === false ? t('printers.form.capsNoPdf') : 'PDF',
              })}
            </Alert>
          )}
        </Stack>
      )}

      <Stack gap={4}>
        <Text fw={500} size="sm">
          {t('printers.form.defaultMargins')}
        </Text>
        <MarginsFields value={state.margins} onChange={(m) => patch({ margins: m })} />
      </Stack>

      <Stack gap="xs">
        <Text fw={500} size="sm">
          {t('printers.form.paperMargins')}
        </Text>
        {Object.entries(state.perPaper).map(([paper, m]) => (
          <Group key={paper} align="flex-end" wrap="nowrap">
            <Text w={90} size="sm" fw={500}>
              {paperOptions.find((o) => o.value === paper)?.label ?? paper}
            </Text>
            <div style={{ flex: 1 }}>
              <MarginsFields
                label={paper}
                value={m}
                onChange={(next) => patch({ perPaper: { ...state.perPaper, [paper]: next } })}
              />
            </div>
            <ActionIcon
              variant="subtle"
              color="red"
              aria-label={t('printers.form.removePaper', { paper })}
              onClick={() => {
                const rest = { ...state.perPaper };
                delete rest[paper];
                patch({ perPaper: rest });
              }}
            >
              ×
            </ActionIcon>
          </Group>
        ))}
        <Select
          size="xs"
          w={260}
          searchable
          placeholder={t('printers.form.addPaper')}
          aria-label={t('printers.form.addPaper')}
          value={null}
          data={addable}
          onChange={(v) =>
            v && patch({ perPaper: { ...state.perPaper, [v]: { ...state.margins } } })
          }
        />
      </Stack>

      {editing && source && (
        <Text size="sm" c="dimmed">
          {t('printers.form.scale', {
            kx: source.scale_kx.toFixed(4),
            ky: source.scale_ky.toFixed(4),
          })}
        </Text>
      )}

      {save.isError && <ErrorAlert error={save.error} />}
      <Group>
        <Button type="submit" loading={save.isPending} disabled={!state.name.trim()}>
          {t('printers.form.save')}
        </Button>
        <Button component={Link} to="/printers" variant="default">
          {t('printers.form.cancel')}
        </Button>
      </Group>
    </Stack>
  );
}
