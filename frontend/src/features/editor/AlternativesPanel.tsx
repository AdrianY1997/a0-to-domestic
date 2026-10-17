import { Button, Group, Stack, Table, Text, Title, UnstyledButton } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import type { Alternative, Plan, Saving } from '../../api/types';
import type { EditorSettings } from '../../lib/settings';
import { alternativeRows, savingPercent } from '../../lib/tiling';

interface Props {
  plan: Plan | undefined;
  open: boolean;
  onToggle: () => void;
  onPatch: (patch: Partial<EditorSettings>) => void;
}

/** Paper x orientation alternatives and savings suggestions (SPEC 5.11). */
export function AlternativesPanel({ plan, open, onToggle, onPatch }: Props) {
  const { t } = useTranslation();
  const alts = plan?.alternatives ?? null;
  const savings = plan?.savings ?? [];
  const rows = alts ? alternativeRows(alts) : [];

  const apply = (a: Alternative) =>
    onPatch({ paper: a.paper_id as EditorSettings['paper'], orientation: a.orientation });
  const applySaving = (sv: Saving) =>
    onPatch({
      scaleMode: 'percent',
      percent: savingPercent(sv),
      orientation: sv.orientation,
    });

  const cell = (a: Alternative | undefined) =>
    a ? (
      <UnstyledButton
        onClick={() => apply(a)}
        aria-pressed={a.current}
        aria-label={t('editor.alternatives.apply', {
          paper: a.paper_id,
          orientation: t(`orientation.${a.orientation}`),
          sheets: a.sheets,
        })}
        data-testid={`alt-${a.paper_id}-${a.orientation}`}
        data-current={a.current}
        style={{
          fontWeight: a.current ? 700 : 400,
          textDecoration: a.current ? 'none' : 'underline',
        }}
      >
        {t('editor.alternatives.cell', { sheets: a.sheets, cols: a.cols, rows: a.rows })}
      </UnstyledButton>
    ) : (
      '–'
    );

  return (
    <Stack gap="xs" component="section" aria-label={t('editor.alternatives.title')}>
      <Group justify="space-between">
        <Title order={2} size="h5">
          {t('editor.alternatives.title')}
        </Title>
        <Button
          size="compact-xs"
          variant="default"
          aria-expanded={open}
          data-testid="alternatives-toggle"
          onClick={onToggle}
        >
          {open ? t('editor.alternatives.hide') : t('editor.alternatives.show')}
        </Button>
      </Group>
      {open && !alts && (
        <Text size="xs" c="dimmed">
          {t('editor.alternatives.loading')}
        </Text>
      )}
      {open &&
        alts &&
        (rows.length === 0 ? (
          <Text size="xs" c="dimmed">
            {t('editor.alternatives.empty')}
          </Text>
        ) : (
          <>
            <Text size="xs" c="dimmed">
              {t('editor.alternatives.scale', { scale: plan?.scale_label ?? '' })}
            </Text>
            <Table withTableBorder withColumnBorders fz="xs" data-testid="alternatives-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t('editor.alternatives.paper')}</Table.Th>
                  <Table.Th>{t('orientation.portrait')}</Table.Th>
                  <Table.Th>{t('orientation.landscape')}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={r.paperId}>
                    <Table.Td>{r.paperId}</Table.Td>
                    <Table.Td>{cell(r.portrait)}</Table.Td>
                    <Table.Td>{cell(r.landscape)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </>
        ))}
      {savings.length > 0 && (
        <Stack gap={4} data-testid="savings">
          <Text size="xs" fw={500}>
            {t('editor.alternatives.savings')}
          </Text>
          {savings.map((sv) => (
            <Button
              key={`${sv.scale}-${sv.orientation}`}
              size="compact-xs"
              variant="light"
              justify="flex-start"
              onClick={() => applySaving(sv)}
            >
              {t('editor.alternatives.saving', {
                scale: sv.scale_label,
                saved: sv.saved,
                sheets: sv.sheets,
                count: sv.saved,
              })}
            </Button>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
