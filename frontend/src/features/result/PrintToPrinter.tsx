import {
  Alert,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { printJob } from '../../api/endpoints';
import { useInfo, useJob, usePrinters } from '../../api/queries';
import type { JobOut, PrintingInfo } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { errorMessage } from '../../lib/errors';
import { parseSheetSelection, type SheetRef } from '../../lib/tiling';

/** Sheets that were actually drawn, across every source page of the job. */
function printedSheets(job: JobOut): SheetRef[] {
  return (job.plans ?? []).flatMap((p) =>
    p.sheets
      .filter((s) => s.print_index !== null)
      .map((s) => ({ id: s.id, row: s.row, col: s.col })),
  );
}

/**
 * "Enviar a impresora" (IMP-03): shown when the server has direct printing enabled and at least
 * one printer profile has an IPP address.
 */
export function PrintToPrinter({ job }: { job: JobOut }) {
  const { t } = useTranslation();
  const info = useInfo();
  const printers = usePrinters();
  const ippPrinters = useMemo(
    () => (printers.data ?? []).filter((p) => !!p.ipp_uri),
    [printers.data],
  );
  const [printerId, setPrinterId] = useState<string | null>(null);
  const [copies, setCopies] = useState<number>(1);
  const [sheetsText, setSheetsText] = useState('');
  const [sentId, setSentId] = useState<string | null>(null);

  const sheets = useMemo(() => printedSheets(job), [job]);
  const selection = useMemo(() => parseSheetSelection(sheetsText, sheets), [sheetsText, sheets]);
  const wanted = sheetsText.trim() !== '';

  const send = useMutation({
    mutationFn: () =>
      printJob(job.id, {
        printer_id: chosen ?? '',
        copies,
        ...(wanted ? { sheets: selection.ids } : {}),
      }),
    onSuccess: (created) => setSentId(created.id),
  });

  if (!info.data?.ipp_enabled || ippPrinters.length === 0) return null;
  if (!job.outputs.some((o) => o.kind === 'pdf')) return null;

  const preferred = job.settings?.target?.printer_id;
  const chosen =
    printerId ?? ippPrinters.find((p) => p.id === preferred)?.id ?? ippPrinters[0]?.id ?? null;
  const invalid = wanted && (selection.invalid.length > 0 || selection.ids.length === 0);

  return (
    <Card withBorder data-testid="print-to-printer">
      <Stack gap="sm">
        <div>
          <Title order={2} size="h4">
            {t('result.ipp.title')}
          </Title>
          <Text size="sm" c="dimmed">
            {t('result.ipp.hint')}
          </Text>
        </div>
        <Group align="flex-start" grow>
          <Select
            label={t('result.ipp.printer')}
            allowDeselect={false}
            value={chosen}
            data={ippPrinters.map((p) => ({ value: p.id, label: p.name }))}
            onChange={setPrinterId}
          />
          <NumberInput
            label={t('result.ipp.copies')}
            min={1}
            max={99}
            allowDecimal={false}
            value={copies}
            onChange={(v) => setCopies(typeof v === 'number' ? v : 1)}
          />
          {!job.test_sheet_only && (
            <TextInput
              label={t('result.ipp.sheets')}
              description={t('result.ipp.sheetsHint')}
              value={sheetsText}
              onChange={(e) => setSheetsText(e.currentTarget.value)}
              error={
                invalid
                  ? t('result.ipp.invalidSheets', {
                      sheets: selection.invalid.join(', ') || sheetsText.trim(),
                    })
                  : undefined
              }
            />
          )}
        </Group>
        <Group>
          <Button
            onClick={() => send.mutate()}
            loading={send.isPending}
            disabled={!chosen || invalid}
            data-testid="send-to-printer"
          >
            {t('result.ipp.send')}
          </Button>
        </Group>
        {send.isError && <ErrorAlert error={send.error} />}
        {sentId && <PrintStatus id={sentId} />}
      </Stack>
    </Card>
  );
}

function PrintStatus({ id }: { id: string }) {
  const { t } = useTranslation();
  const job = useJob(id).data;
  if (!job) return null;
  const printing = (job.printing ?? {}) as PrintingInfo;
  const printerJob = printing.printer_job_id ?? '';
  if (job.status === 'failed') {
    return (
      <Alert color="red" title={t('result.ipp.failed')} role="alert" data-testid="print-failed">
        {errorMessage(t, job.error_code, job.error_params)}
      </Alert>
    );
  }
  if (job.status === 'succeeded') {
    const key = printing.printer_job_state === 'completed' ? 'completed' : 'unknown';
    return (
      <Alert color="green" role="status" data-testid="print-done">
        {t(`result.ipp.${key}`, { id: printerJob })}
      </Alert>
    );
  }
  if (job.status === 'cancelled') return null;
  return (
    <Text size="sm" role="status" aria-live="polite" data-testid="print-progress">
      {t('progress.print.printing')} {Math.round(job.progress)} %
    </Text>
  );
}
