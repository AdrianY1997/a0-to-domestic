import {
  Alert,
  Button,
  Group,
  NumberInput,
  Select,
  Stack,
  Stepper,
  Text,
  Title,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { usePapers, usePrinters } from '../../api/queries';
import type { Margins } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { useAppStore } from '../../store/app';
import { calibratePrinter, calibrationPdfUrl } from './api';
import { K_MAX, K_MIN, kInRange, nominalLengthsMm, scaleFactor } from './calibration';
import { MarginsFields } from './MarginsFields';

export default function CalibrationPage() {
  const { id = '' } = useParams();
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const printers = usePrinters();
  const papers = usePapers();
  const defaultPaper = useAppStore((s) => s.defaultPaper);
  const printer = printers.data?.find((p) => p.id === id);

  const [step, setStep] = useState(0);
  const [paperId, setPaperId] = useState<string>(defaultPaper);
  const [measuredX, setMeasuredX] = useState<number | null>(null);
  const [measuredY, setMeasuredY] = useState<number | null>(null);
  const [margins, setMargins] = useState<Margins>({ top: 0, right: 0, bottom: 0, left: 0 });
  const [marginsTouched, setMarginsTouched] = useState(false);

  const paper = papers.data?.find((p) => p.id === paperId);
  const [nomX, nomY] = useMemo(
    () => (paper ? nominalLengthsMm(paper.width_mm, paper.height_mm) : [0, 0]),
    [paper],
  );
  const mx = measuredX ?? 0;
  const my = measuredY ?? 0;
  const measured = measuredX !== null && measuredY !== null && mx > 0 && my > 0;
  const kx = measured ? scaleFactor(mx, nomX) : NaN;
  const ky = measured ? scaleFactor(my, nomY) : NaN;
  const outOfRange = measured && (!kInRange(kx) || !kInRange(ky));

  const save = useMutation({
    mutationFn: () =>
      calibratePrinter(id, {
        paper: paperId,
        measured_x_mm: mx,
        measured_y_mm: my,
        nominal_x_mm: nomX,
        nominal_y_mm: nomY,
        margins_mm: margins,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['printers'] });
      setStep(2);
    },
  });

  if (printers.isPending) return <Text>{t('printers.loading')}</Text>;
  if (!printer) return <Alert color="red">{t('printers.cal.notFound')}</Alert>;
  if (printer.builtin) {
    return (
      <Stack maw={720} mx="auto">
        <Alert color="blue">{t('printers.cal.builtinBlocked')}</Alert>
        <Button w="fit-content" onClick={() => void navigate(`/printers/new?from=${printer.id}`)}>
          {t('printers.duplicate')}
        </Button>
      </Stack>
    );
  }

  const paperOptions = (papers.data ?? [])
    .filter((p) => !printer.supported_papers || printer.supported_papers.some((s) => s === p.id))
    .map((p) => ({ value: p.id, label: p.name }));
  const saved = save.data;
  const sm = saved?.paper_margins?.[paperId];

  return (
    <Stack maw={720} mx="auto" gap="md">
      <Title order={1}>{t('printers.cal.title', { name: printer.name })}</Title>
      <Stepper active={step} allowNextStepsSelect={false}>
        <Stepper.Step label={t('printers.cal.steps.print')} />
        <Stepper.Step label={t('printers.cal.steps.measure')} />
        <Stepper.Step label={t('printers.cal.steps.done')} />
      </Stepper>

      {step === 0 && (
        <Stack>
          <Select
            label={t('printers.cal.paper')}
            allowDeselect={false}
            searchable
            value={paperId}
            data={paperOptions}
            onChange={(v) => v && setPaperId(v)}
          />
          <Text size="sm">{t('printers.cal.printHelp')}</Text>
          <Group>
            <Button
              component="a"
              href={calibrationPdfUrl(id, paperId, i18n.resolvedLanguage ?? 'es')}
              target="_blank"
              rel="noopener"
              variant="default"
            >
              {t('printers.cal.download')}
            </Button>
            <Button onClick={() => setStep(1)}>{t('printers.cal.next')}</Button>
          </Group>
        </Stack>
      )}

      {step === 1 && (
        <Stack>
          <Group grow align="flex-start">
            <NumberInput
              label={t('printers.cal.measuredX')}
              description={t('printers.cal.nominalX', { mm: nomX })}
              min={1}
              decimalScale={2}
              value={measuredX ?? ''}
              onChange={(v) => setMeasuredX(typeof v === 'number' ? v : null)}
            />
            <NumberInput
              label={t('printers.cal.measuredY')}
              description={t('printers.cal.nominalY', { mm: nomY })}
              min={1}
              decimalScale={2}
              value={measuredY ?? ''}
              onChange={(v) => setMeasuredY(typeof v === 'number' ? v : null)}
            />
          </Group>
          <Text size="sm">{t('printers.cal.marginsHelp')}</Text>
          <MarginsFields
            value={margins}
            onChange={(m) => {
              setMargins(m);
              setMarginsTouched(true);
            }}
          />
          <Text size="sm" data-testid="k-result">
            {t('printers.cal.kResult', {
              kx: Number.isFinite(kx) ? kx.toFixed(4) : '–',
              ky: Number.isFinite(ky) ? ky.toFixed(4) : '–',
            })}
          </Text>
          {outOfRange && (
            <Alert color="orange" role="alert" data-testid="k-warning">
              {t('printers.cal.outOfRange', { min: K_MIN, max: K_MAX })}
            </Alert>
          )}
          {save.isError && <ErrorAlert error={save.error} />}
          <Group>
            <Button variant="default" onClick={() => setStep(0)}>
              {t('printers.cal.back')}
            </Button>
            <Button
              loading={save.isPending}
              disabled={outOfRange || !paper || !measured || !marginsTouched}
              onClick={() => save.mutate()}
            >
              {t('printers.cal.save')}
            </Button>
          </Group>
        </Stack>
      )}

      {step === 2 && saved && (
        <Stack>
          <Alert color="green" role="status">
            {t('printers.cal.saved', {
              top: sm?.top,
              right: sm?.right,
              bottom: sm?.bottom,
              left: sm?.left,
              kx: saved.scale_kx.toFixed(4),
              ky: saved.scale_ky.toFixed(4),
            })}
          </Alert>
          <Button component={Link} to="/printers" w="fit-content">
            {t('printers.cal.backToList')}
          </Button>
        </Stack>
      )}
    </Stack>
  );
}
