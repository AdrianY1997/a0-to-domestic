import { Alert, Button, Group, Loader, Text } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import type { Plan } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { useFormat } from '../../lib/useFormat';

interface Props {
  plan: Plan | undefined;
  planError: unknown;
  fetching: boolean;
  busy: boolean;
  jobError: unknown;
  onTestSheet: () => void;
  onGenerate: () => void;
}

/** Bottom bar: "21 hojas A4 horizontal · 3×7 · 100 % · 841×1189 mm" + actions (SPEC 8.2). */
export function SummaryBar({
  plan,
  planError,
  fetching,
  busy,
  jobError,
  onTestSheet,
  onGenerate,
}: Props) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const disabled = !plan || !!planError || busy || plan.counts.printed === 0;
  return (
    <div>
      {!!planError && <ErrorAlert error={planError} title={t('editor.summary.planError')} />}
      {!!jobError && <ErrorAlert error={jobError} />}
      {plan && plan.counts.printed === 0 && !planError && (
        <Alert color="yellow" p="xs" mb="xs">
          {t('editor.summary.noSheets')}
        </Alert>
      )}
      <Group justify="space-between" wrap="wrap" mt={planError || jobError ? 'xs' : 0}>
        <Group gap="xs" aria-live="polite" data-testid="summary-text">
          {plan ? (
            <Text fw={500} size="sm">
              {t('editor.summary.sheets', {
                count: plan.counts.printed,
                paper: plan.paper.id,
                orientation: t(`orientation.${plan.orientation}`),
              })}
              {' · '}
              {plan.grid.cols}×{plan.grid.rows}
              {' · '}
              {plan.scale_label}
              {' · '}
              {fmt.size(plan.assembled_size_mm[0], plan.assembled_size_mm[1])}
              {plan.counts.blank > 0 &&
                ` · ${t('editor.summary.blank', { count: plan.counts.blank })}`}
            </Text>
          ) : (
            <Text size="sm" c="dimmed">
              {t('editor.summary.calculating')}
            </Text>
          )}
          {plan?.effective_dpi ? (
            <Text size="xs" c="dimmed" data-testid="effective-dpi">
              {fmt.number(plan.effective_dpi, 1)} DPI
            </Text>
          ) : null}
          {fetching && <Loader size="xs" aria-label={t('editor.summary.calculating')} />}
        </Group>
        <Group gap="xs">
          <Button variant="default" onClick={onTestSheet} disabled={!plan || !!planError || busy}>
            {t('editor.summary.testSheet')}
          </Button>
          <Button
            onClick={onGenerate}
            disabled={disabled}
            loading={busy}
            data-testid="generate"
          >
            {t('editor.summary.generate')}
          </Button>
        </Group>
      </Group>
    </div>
  );
}
