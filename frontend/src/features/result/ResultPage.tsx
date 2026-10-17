import {
  Alert,
  Button,
  Card,
  Group,
  Image,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { cancelJob, createJob, jobEventsUrl, outputThumbUrl, outputUrl } from '../../api/endpoints';
import { useJob } from '../../api/queries';
import { useEventStream } from '../../api/sse';
import type { InkEstimate, JobOut, OutputFile } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { errorMessage } from '../../lib/errors';
import { progressMessage } from '../../lib/progress';
import { useFormat } from '../../lib/useFormat';
import { SaveAsPreset } from '../presets/SaveAsPreset';
import { PrintInstructions, PrintInstructionsModal } from './PrintInstructions';
import { PrintToPrinter } from './PrintToPrinter';

const MAX_THUMBS = 12;

export default function ResultPage() {
  const { id = '' } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const jobQ = useJob(id);
  const job = jobQ.data;
  const active = job?.status === 'queued' || job?.status === 'running';
  const sse = useEventStream(jobEventsUrl(id), active);

  useEffect(() => {
    if (sse.done || sse.error) void qc.invalidateQueries({ queryKey: ['job', id] });
  }, [sse.done, sse.error, qc, id]);

  const cancel = useMutation({
    mutationFn: () => cancelJob(id),
    onSuccess: (j) => qc.setQueryData(['job', id], j),
  });

  if (jobQ.isError) {
    return (
      <Stack maw={640} mx="auto">
        <ErrorAlert error={jobQ.error} />
        <Button component={Link} to="/" variant="default" w="fit-content">
          {t('common.backHome')}
        </Button>
      </Stack>
    );
  }
  if (!job) return null;

  return (
    <Stack maw={1000} mx="auto" gap="lg">
      <Title order={1}>{job.test_sheet_only ? t('result.testTitle') : t('result.title')}</Title>

      {active && (
        <Card withBorder aria-live="polite">
          <Stack gap="xs">
            <Text>{progressMessage(t, sse.progress)}</Text>
            <Progress
              value={sse.progress?.pct ?? job.progress}
              animated
              aria-label={t('result.progress')}
            />
            <Group>
              <Button
                variant="default"
                size="xs"
                onClick={() => cancel.mutate()}
                loading={cancel.isPending}
              >
                {t('result.cancel')}
              </Button>
            </Group>
          </Stack>
        </Card>
      )}

      {job.status === 'failed' && (
        <Alert color="red" title={t('result.failed')} role="alert" data-testid="job-failed">
          {errorMessage(t, job.error_code, job.error_params)}
        </Alert>
      )}
      {job.status === 'cancelled' && (
        <Alert color="yellow" role="status">
          {t('result.cancelled')}
        </Alert>
      )}

      {job.status === 'succeeded' && <Outputs job={job} />}

      <Group>
        <Button component={Link} to={`/documents/${job.document_id}`} variant="default">
          {t('result.backToEditor')}
        </Button>
      </Group>
    </Stack>
  );
}

function Outputs({ job }: { job: JobOut }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [instructionsOpen, { open, close }] = useDisclosure(false);
  const paper = job.plans?.[0]?.paper.id;
  const join = job.settings?.tiling?.join;

  const again = useMutation({
    mutationFn: (testSheetOnly: boolean) => {
      if (!job.settings) throw new Error('no settings');
      return createJob({ settings: job.settings, test_sheet_only: testSheetOnly });
    },
    onSuccess: (created) => void navigate(`/jobs/${created.id}`),
  });

  const printPdf = (out: OutputFile) => {
    // IMP-01: open the PDF in the browser's viewer and show how to print it.
    window.open(outputUrl(job.id, out.name, true), '_blank', 'noopener');
    open();
  };

  return (
    <>
      {job.test_sheet_only && (
        <Alert color="blue" title={t('result.testHint.title')}>
          {t('result.testHint.text')}
        </Alert>
      )}
      <Stack gap="md">
        {job.outputs.map((out) => (
          <OutputCard key={out.name} job={job} out={out} onPrint={() => printPdf(out)} />
        ))}
      </Stack>
      <PrintToPrinter job={job} />
      <Group>
        {job.test_sheet_only ? (
          <Button onClick={() => again.mutate(false)} loading={again.isPending}>
            {t('result.generateFull')}
          </Button>
        ) : (
          <Button variant="light" onClick={() => again.mutate(true)} loading={again.isPending}>
            {t('result.printTestFirst')}
          </Button>
        )}
        {job.settings && <SaveAsPreset settings={job.settings} />}
      </Group>
      {again.isError && <ErrorAlert error={again.error} />}
      <InkSummary ink={job.ink as InkEstimate | null | undefined} />
      <Card withBorder>
        <Title order={2} size="h4" mb="sm">
          {t('instructions.title')}
        </Title>
        <PrintInstructions paper={paper} join={join} />
      </Card>
      <PrintInstructionsModal opened={instructionsOpen} onClose={close} paper={paper} join={join} />
    </>
  );
}

/** COL-07: estimated ink coverage per sheet and in total. */
function InkSummary({ ink }: { ink: InkEstimate | null | undefined }) {
  const { t } = useTranslation();
  const fmt = useFormat();
  if (!ink || ink.sheets.length === 0) return null;
  return (
    <Card withBorder data-testid="ink-summary">
      <Title order={2} size="h4" mb="xs">
        {t('result.ink.title')}
      </Title>
      <Text size="sm">
        {t('result.ink.total', {
          pct: fmt.number(ink.total_coverage * 100, 1),
          area: fmt.number(ink.total_equiv_cm2, 0),
        })}
      </Text>
      <Text size="xs" c="dimmed" mt={4}>
        {ink.sheets.map((sh) => `${sh.id}: ${fmt.number(sh.coverage * 100, 1)} %`).join(' · ')}
      </Text>
      <Text size="xs" c="dimmed" mt={4}>
        {t('result.ink.note')}
      </Text>
    </Card>
  );
}

function OutputCard({ job, out, onPrint }: { job: JobOut; out: OutputFile; onPrint: () => void }) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const isPdf = out.kind === 'pdf';
  const pages = out.pages ?? 0;
  const thumbs = Math.min(pages, MAX_THUMBS);
  return (
    <Card withBorder component="section" aria-label={out.name}>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text fw={600} style={{ wordBreak: 'break-all' }}>
              {out.name}
            </Text>
            <Text size="sm" c="dimmed">
              {fmt.number(out.size_bytes / (1024 * 1024), 2)} MB
              {pages > 0 && ` · ${t('result.pages', { count: pages })}`}
            </Text>
          </div>
          <Group gap="xs">
            <Button
              component="a"
              href={outputUrl(job.id, out.name)}
              download={out.name}
              data-testid="download"
            >
              {t('result.download')}
            </Button>
            {isPdf && (
              <Button variant="light" onClick={onPrint}>
                {t('result.print')}
              </Button>
            )}
          </Group>
        </Group>
        {isPdf && thumbs > 0 && (
          <SimpleGrid cols={{ base: 3, sm: 4, md: 6 }} spacing="xs">
            {Array.from({ length: thumbs }, (_, n) => (
              <Image
                key={n}
                src={outputThumbUrl(job.id, out.name, n)}
                alt={t('result.pageThumb', { n: n + 1 })}
                loading="lazy"
                fit="contain"
                h={110}
                style={{ border: '1px solid var(--mantine-color-default-border)' }}
              />
            ))}
          </SimpleGrid>
        )}
        {isPdf && pages > thumbs && (
          <Text size="xs" c="dimmed">
            {t('result.morePages', { count: pages - thumbs })}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
