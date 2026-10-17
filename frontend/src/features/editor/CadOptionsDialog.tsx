import {
  Button,
  Group,
  Modal,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { setCadOptions } from '../../api/endpoints';
import type { CadColor, CadInfo, CadLayout, CadOptions } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { useFormat } from '../../lib/useFormat';

/** Formats the dialog applies to (SPEC 7.1: the UI asks for the layout, units, scale, colour). */
export const CAD_FORMATS = ['dxf', 'dwg'];

/** Drawing units offered for model space (`null` = the file's own `$INSUNITS`). */
export const CAD_UNITS = ['mm', 'cm', 'm', 'in', 'ft'] as const;
const AUTO = 'auto';

/** The `POST /documents/{id}/cad-options` body for the dialog's values. */
export function buildCadOptions(v: {
  layout: string;
  units: string;
  scale: number | '';
  color: CadColor;
  isModel: boolean;
}): CadOptions {
  return {
    layout: v.layout,
    // Units and scale only mean something in model space; paper space has its own page size.
    units: v.isModel && v.units !== AUTO ? v.units : null,
    scale: v.isModel && typeof v.scale === 'number' && v.scale > 0 ? v.scale : null,
    color: v.color,
  };
}

function layoutLabel(
  l: CadLayout,
  t: (k: string) => string,
  size: (w: number, h: number) => string,
): string {
  if (l.kind === 'model') return `${l.name} (${t('cad.modelSpace')})`;
  return l.width_mm && l.height_mm ? `${l.name} · ${size(l.width_mm, l.height_mm)}` : l.name;
}

interface Props {
  documentId: string;
  info: CadInfo | undefined;
  opened: boolean;
  onClose: () => void;
}

export function CadOptionsDialog({ documentId, info, opened, onClose }: Props) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const qc = useQueryClient();
  const [layout, setLayout] = useState<string | null>(null);
  const [units, setUnits] = useState<string | null>(null);
  const [scale, setScale] = useState<number | ''>('');
  const [color, setColor] = useState<CadColor | null>(null);

  // Fall back to what the converter chose until the user changes a field.
  const layouts = info?.layouts ?? [];
  const currentLayout = layout ?? info?.layout ?? '';
  const isModel = layouts.find((l) => l.name === currentLayout)?.kind !== 'paper';
  const currentColor = color ?? info?.color ?? 'color';
  const currentUnits = units ?? (info?.units_source === 'user' ? info.units : AUTO);
  const currentScale = scale === '' && info?.scale && !info.scale_auto ? info.scale : scale;

  const save = useMutation({
    mutationFn: () =>
      setCadOptions(
        documentId,
        buildCadOptions({
          layout: currentLayout,
          units: currentUnits,
          scale: currentScale,
          color: currentColor,
          isModel,
        }),
      ),
    onSuccess: async (doc) => {
      qc.setQueryData(['document', documentId], doc);
      await qc.invalidateQueries({ queryKey: ['cad', documentId] });
      await qc.invalidateQueries({ queryKey: ['plan'] });
      onClose();
    },
  });

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      data-testid="cad-options-dialog"
      title={t('cad.title')}
      closeButtonProps={{ 'aria-label': t('common.close') }}
    >
      <Stack>
        <Text size="sm">{t('cad.message')}</Text>
        <Select
          label={t('cad.layout')}
          allowDeselect={false}
          value={currentLayout}
          onChange={(v) => v && setLayout(v)}
          data={layouts.map((l) => ({
            value: l.name,
            label: layoutLabel(l, t, (w, h) => fmt.size(w, h)),
          }))}
        />
        {isModel ? (
          <>
            <Select
              label={t('cad.units')}
              description={
                info && currentUnits === AUTO
                  ? t(`cad.detected.${info.units_source}`, { units: info.units })
                  : undefined
              }
              allowDeselect={false}
              value={currentUnits}
              onChange={(v) => v && setUnits(v)}
              data={[
                { value: AUTO, label: t('cad.unitsAuto') },
                ...CAD_UNITS.map((u) => ({ value: u, label: u })),
              ]}
            />
            <NumberInput
              label={t('cad.scale')}
              description={
                info?.scale_auto && info.scale
                  ? t('cad.scaleAuto', { scale: info.scale })
                  : t('cad.scaleHint')
              }
              leftSection="1 :"
              leftSectionWidth={36}
              min={0}
              max={1_000_000}
              allowNegative={false}
              decimalScale={2}
              placeholder={t('cad.scaleAutoPlaceholder')}
              value={currentScale}
              onChange={(v) => setScale(typeof v === 'number' ? v : '')}
            />
          </>
        ) : (
          <Text size="sm" c="dimmed">
            {t('cad.paperSpaceNote')}
          </Text>
        )}
        <SegmentedControl
          fullWidth
          size="xs"
          value={currentColor}
          onChange={(v) => setColor(v as CadColor)}
          aria-label={t('cad.color')}
          data={(['color', 'black', 'gray'] as const).map((c) => ({
            value: c,
            label: t(`cad.colors.${c}`),
          }))}
        />
        {save.isError && <ErrorAlert error={save.error} />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button loading={save.isPending} disabled={!info} onClick={() => save.mutate()}>
            {t('cad.apply')}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
