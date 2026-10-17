import { ActionIcon, Badge, Button, Group, Select, Stack, Table, Text, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { usePrinters } from '../../api/queries';
import { ErrorAlert } from '../../components/ErrorAlert';
import type { DefaultPaper } from '../../lib/region';
import { LENGTH_UNITS, type LengthUnit } from '../../lib/units';
import { useAppStore } from '../../store/app';
import { deletePrinter } from './api';

const fmtK = (k: number) => k.toFixed(4);

export default function PrintersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const printers = usePrinters();
  const unit = useAppStore((s) => s.unit);
  const setUnit = useAppStore((s) => s.setUnit);
  const defaultPaper = useAppStore((s) => s.defaultPaper);
  const setDefaultPaper = useAppStore((s) => s.setDefaultPaper);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: deletePrinter,
    onSuccess: () => {
      setConfirmId(null);
      return qc.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  return (
    <Stack maw={960} mx="auto" gap="lg">
      <Group justify="space-between">
        <Title order={1}>{t('printers.title')}</Title>
        <Button component={Link} to="/printers/new">
          {t('printers.new')}
        </Button>
      </Group>
      <Text size="sm" c="dimmed">
        {t('printers.intro')}
      </Text>

      {printers.isError && <ErrorAlert error={printers.error} />}
      {remove.isError && <ErrorAlert error={remove.error} />}
      {printers.isPending && <Text>{t('printers.loading')}</Text>}

      {printers.data && (
        <Table withTableBorder aria-label={t('printers.title')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('printers.colName')}</Table.Th>
              <Table.Th>{t('printers.colKind')}</Table.Th>
              <Table.Th>{t('printers.colMargins')}</Table.Th>
              <Table.Th>{t('printers.colScale')}</Table.Th>
              <Table.Th>{t('printers.colActions')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {printers.data.map((p) => {
              const m = p.default_margins;
              const calibrated = p.scale_kx !== 1 || p.scale_ky !== 1;
              return (
                <Table.Tr key={p.id} data-testid={`printer-${p.id}`}>
                  <Table.Td>
                    <Group gap="xs">
                      <Text fw={500}>{p.name}</Text>
                      {p.builtin && (
                        <Badge variant="light" size="sm">
                          {t('printers.builtin')}
                        </Badge>
                      )}
                    </Group>
                    <Text size="xs" c="dimmed">
                      {p.color ? t('printers.color') : t('printers.mono')}
                      {p.borderless ? ` · ${t('printers.borderless')}` : ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>{t(`printers.kind.${p.kind}`)}</Table.Td>
                  <Table.Td>
                    {m ? `${m.top} / ${m.right} / ${m.bottom} / ${m.left}` : '5 / 5 / 5 / 5'}
                  </Table.Td>
                  <Table.Td>
                    {calibrated
                      ? `${t('printers.calibrated')} ${fmtK(p.scale_kx)} × ${fmtK(p.scale_ky)}`
                      : t('printers.uncalibrated')}
                  </Table.Td>
                  <Table.Td>
                    {confirmId === p.id ? (
                      <Group gap="xs" role="alertdialog" aria-label={t('printers.delete')}>
                        <Text size="xs">{t('printers.confirmDelete', { name: p.name })}</Text>
                        <Button
                          size="xs"
                          color="red"
                          loading={remove.isPending}
                          onClick={() => remove.mutate(p.id)}
                        >
                          {t('printers.delete')}
                        </Button>
                        <Button size="xs" variant="default" onClick={() => setConfirmId(null)}>
                          {t('common.cancel')}
                        </Button>
                      </Group>
                    ) : (
                      <Group gap="xs">
                        {p.builtin ? (
                          <Button
                            size="xs"
                            variant="default"
                            onClick={() => void navigate(`/printers/new?from=${p.id}`)}
                          >
                            {t('printers.duplicate')}
                          </Button>
                        ) : (
                          <>
                            <Button
                              size="xs"
                              variant="default"
                              component={Link}
                              to={`/printers/${p.id}`}
                            >
                              {t('printers.edit')}
                            </Button>
                            <Button
                              size="xs"
                              variant="default"
                              component={Link}
                              to={`/printers/${p.id}/calibrate`}
                            >
                              {t('printers.calibrate')}
                            </Button>
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              aria-label={`${t('printers.delete')} ${p.name}`}
                              onClick={() => setConfirmId(p.id)}
                            >
                              ×
                            </ActionIcon>
                          </>
                        )}
                      </Group>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}

      <Stack gap="xs" component="section">
        <Title order={2} size="h4">
          {t('printers.defaults.title')}
        </Title>
        <Group>
          <Select
            label={t('printers.defaults.paper')}
            w={180}
            allowDeselect={false}
            value={defaultPaper}
            data={[
              { value: 'A4', label: 'A4' },
              { value: 'LETTER', label: 'Carta / Letter' },
            ]}
            onChange={(v) => v && setDefaultPaper(v as DefaultPaper)}
          />
          <Select
            label={t('printers.defaults.unit')}
            w={120}
            allowDeselect={false}
            value={unit}
            data={LENGTH_UNITS.map((u) => ({ value: u, label: u }))}
            onChange={(v) => v && setUnit(v as LengthUnit)}
          />
        </Group>
        <Text size="xs" c="dimmed">
          {t('printers.defaults.hint')}
        </Text>
      </Stack>
    </Stack>
  );
}
