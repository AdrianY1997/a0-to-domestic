import {
  Accordion,
  Group,
  NumberInput,
  Radio,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { usePapers, usePresets, usePrinters } from '../../api/queries';
import type {
  Align,
  AssemblyPage,
  BlankSensitivity,
  NamingScheme,
  Page,
  Orientation,
  PaperId,
  Rotation,
  SheetOrder,
  SourceBox,
  TestSquare,
} from '../../api/types';
import { LengthInput } from '../../components/LengthInput';
import {
  DEFAULT_FILENAME_TEMPLATE,
  layerVisible,
  OUTPUT_DPIS,
  OVERLAP_LIMITS,
  PAPER_CUSTOM_LIMITS,
  ROLL_LIMITS,
  SCALE_MODES,
  toggleLayer,
  type EditorSettings,
  type OutputDpi,
  type ScaleMode,
} from '../../lib/settings';
import { ratioLabel } from '../../lib/tiling';
import { useFormat } from '../../lib/useFormat';

interface Props {
  settings: EditorSettings;
  onPatch: (patch: Partial<EditorSettings>) => void;
  presetId: string | null;
  onPreset: (presetId: string | null, settings: unknown) => void;
  pageCount: number;
  /** The page being edited: its layers (ANA-07) and detected drawing scale (ANA-09). */
  page?: Page;
  scaleLabel?: string;
  /** Resulting scale factor (1 = 100 %), to show the 1:N / percent equivalent. */
  scaleFactor?: number;
}

/** Options for a Select/SegmentedControl from a translation prefix. */
function opts<T extends string | number>(
  t: (k: string) => string,
  prefix: string,
  values: readonly T[],
) {
  return values.map((v) => ({ value: String(v), label: t(`${prefix}.${v}`) }));
}

export function SettingsPanel({
  settings: s,
  onPatch,
  presetId,
  onPreset,
  pageCount,
  page,
  scaleLabel,
  scaleFactor,
}: Props) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const printers = usePrinters();
  const papers = usePapers();
  const presets = usePresets();

  const printer = printers.data?.find((p) => p.id === s.printerId);
  const paperOptions = useMemo(() => {
    const supported = printer?.supported_papers;
    return (papers.data ?? [])
      .filter((p) => !supported || supported.length === 0 || supported.includes(p.id as PaperId))
      .map((p) => ({
        value: p.id,
        label: `${p.name} (${fmt.size(p.width_mm, p.height_mm, 0)})`,
      }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [papers.data, printer, fmt.unit, fmt.locale]);
  const paperOptionsWithCustom = [
    ...paperOptions,
    { value: 'CUSTOM', label: t('settings.paper.custom') },
    { value: 'ROLL', label: t('settings.paper.banner') },
  ];

  const butt = s.join === 'butt';
  const layers = page?.layers ?? [];

  return (
    <Stack gap="sm" component="section" aria-label={t('editor.settings.title')}>
      <Select
        label={t('editor.settings.preset')}
        size="xs"
        placeholder={t('editor.settings.presetPlaceholder')}
        clearable
        value={presetId}
        data={(presets.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
        onChange={(id) => {
          const preset = presets.data?.find((p) => p.id === id);
          onPreset(id, preset?.settings ?? {});
        }}
      />
      <Accordion
        multiple
        defaultValue={['paper', 'scale']}
        variant="separated"
        chevronPosition="left"
      >
        <Accordion.Item value="paper">
          <Accordion.Control>{t('settings.sections.paper')}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="xs">
              <Select
                label={t('settings.printer.label')}
                size="xs"
                allowDeselect={false}
                value={s.printerId}
                data={(printers.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
                onChange={(v) => v && onPatch({ printerId: v })}
              />
              <Select
                label={t('settings.paper.label')}
                size="xs"
                allowDeselect={false}
                searchable
                value={s.banner ? 'ROLL' : s.paper}
                data={paperOptionsWithCustom}
                onChange={(v) => {
                  if (!v) return;
                  if (v === 'ROLL') onPatch({ banner: true });
                  else onPatch({ banner: false, paper: v as PaperId });
                }}
              />
              {s.banner && (
                <>
                  <SimpleGrid cols={2}>
                    <LengthInput
                      label={t('settings.paper.rollWidth')}
                      valueMm={s.rollWidthMm}
                      minMm={ROLL_LIMITS.minW}
                      maxMm={ROLL_LIMITS.maxW}
                      onChangeMm={(v) => onPatch({ rollWidthMm: v })}
                    />
                    <LengthInput
                      label={t('settings.paper.rollMaxLength')}
                      valueMm={s.rollMaxLengthMm}
                      minMm={ROLL_LIMITS.minL}
                      maxMm={ROLL_LIMITS.maxL}
                      onChangeMm={(v) => onPatch({ rollMaxLengthMm: v })}
                    />
                  </SimpleGrid>
                  <Text size="xs" c="dimmed">
                    {t('settings.paper.rollHint')}
                  </Text>
                </>
              )}
              {s.paper === 'CUSTOM' && (
                <SimpleGrid cols={2}>
                  <LengthInput
                    label={t('settings.paper.width')}
                    valueMm={s.customPaperWidthMm}
                    minMm={PAPER_CUSTOM_LIMITS.minW}
                    maxMm={PAPER_CUSTOM_LIMITS.maxW}
                    onChangeMm={(v) => onPatch({ customPaperWidthMm: v })}
                  />
                  <LengthInput
                    label={t('settings.paper.height')}
                    valueMm={s.customPaperHeightMm}
                    minMm={PAPER_CUSTOM_LIMITS.minH}
                    maxMm={PAPER_CUSTOM_LIMITS.maxH}
                    onChangeMm={(v) => onPatch({ customPaperHeightMm: v })}
                  />
                </SimpleGrid>
              )}
              {!s.banner && (
                <div>
                  <Text size="xs" fw={500} id="orientation-label">
                    {t('settings.orientation.label')}
                  </Text>
                  <SegmentedControl
                    fullWidth
                    size="xs"
                    aria-labelledby="orientation-label"
                    value={s.orientation}
                    onChange={(v) => onPatch({ orientation: v as Orientation })}
                    data={opts(t, 'settings.orientation', ['auto', 'portrait', 'landscape'])}
                  />
                </div>
              )}
              <Switch
                size="xs"
                label={t('settings.margins.custom')}
                checked={s.customMargins}
                onChange={(e) => onPatch({ customMargins: e.currentTarget.checked })}
              />
              {s.customMargins && (
                <SimpleGrid cols={2}>
                  {(['top', 'right', 'bottom', 'left'] as const).map((side) => (
                    <LengthInput
                      key={side}
                      label={t(`settings.margins.${side}`)}
                      valueMm={s.marginsMm[side]}
                      minMm={0}
                      maxMm={50}
                      onChangeMm={(v) => onPatch({ marginsMm: { ...s.marginsMm, [side]: v } })}
                    />
                  ))}
                </SimpleGrid>
              )}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>

        <Accordion.Item value="scale">
          <Accordion.Control>{t('settings.sections.scale')}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="xs">
              <Radio.Group
                value={s.scaleMode}
                onChange={(v) => {
                  const detected = page?.detected_scale;
                  // ANA-09: prefill the drawing scale with the one found in the page text
                  onPatch(
                    v === 'drawing_scale' && detected
                      ? { scaleMode: 'drawing_scale', drawingFrom: detected }
                      : { scaleMode: v as ScaleMode },
                  );
                }}
                label={t('settings.scale.mode')}
                size="xs"
              >
                <Stack gap={4} mt={4}>
                  {SCALE_MODES.map((m) => (
                    <Radio
                      key={m}
                      value={m}
                      data-testid={`scale-mode-${m}`}
                      label={t(`settings.scale.modes.${m}`)}
                    />
                  ))}
                </Stack>
              </Radio.Group>
              {s.scaleMode === 'percent' && (
                <NumberInput
                  label={t('settings.scale.percent')}
                  size="xs"
                  value={s.percent}
                  min={1}
                  max={1000}
                  decimalScale={2}
                  suffix=" %"
                  onChange={(v) => typeof v === 'number' && onPatch({ percent: v })}
                />
              )}
              {s.scaleMode === 'fit_grid' && (
                <SimpleGrid cols={2}>
                  <NumberInput
                    label={t('settings.scale.cols')}
                    size="xs"
                    min={1}
                    max={50}
                    allowDecimal={false}
                    value={s.fitCols}
                    onChange={(v) => typeof v === 'number' && onPatch({ fitCols: v })}
                  />
                  <NumberInput
                    label={t('settings.scale.rows')}
                    size="xs"
                    min={1}
                    max={50}
                    allowDecimal={false}
                    value={s.fitRows}
                    onChange={(v) => typeof v === 'number' && onPatch({ fitRows: v })}
                  />
                </SimpleGrid>
              )}
              {s.scaleMode === 'fit_count' && (
                <NumberInput
                  label={t('settings.scale.maxSheets')}
                  size="xs"
                  min={1}
                  max={500}
                  allowDecimal={false}
                  value={s.maxSheets}
                  onChange={(v) => typeof v === 'number' && onPatch({ maxSheets: v })}
                />
              )}
              {s.scaleMode === 'final_size' && (
                <Group grow align="flex-end">
                  <Select
                    label={t('settings.scale.finalDimension')}
                    size="xs"
                    allowDeselect={false}
                    value={s.finalDimension}
                    data={opts(t, 'settings.scale.dimension', ['width', 'height'])}
                    onChange={(v) => v && onPatch({ finalDimension: v as 'width' | 'height' })}
                  />
                  <LengthInput
                    label={t('settings.scale.finalSize')}
                    valueMm={s.finalSizeMm}
                    minMm={10}
                    onChangeMm={(v) => onPatch({ finalSizeMm: v })}
                  />
                </Group>
              )}
              {s.scaleMode === 'drawing_scale' && page?.detected_scale && (
                <Text size="xs" c="dimmed" data-testid="detected-scale">
                  {t('settings.scale.detected', { n: page.detected_scale })}
                </Text>
              )}
              {s.scaleMode === 'drawing_scale' && (
                <Group grow align="flex-end">
                  <NumberInput
                    label={t('settings.scale.drawingFrom')}
                    size="xs"
                    prefix="1:"
                    min={1}
                    value={s.drawingFrom}
                    onChange={(v) => typeof v === 'number' && onPatch({ drawingFrom: v })}
                  />
                  <NumberInput
                    label={t('settings.scale.drawingTo')}
                    size="xs"
                    prefix="1:"
                    min={1}
                    value={s.drawingTo}
                    onChange={(v) => typeof v === 'number' && onPatch({ drawingTo: v })}
                  />
                </Group>
              )}
              {scaleLabel && (
                <Text size="xs" c="dimmed" aria-live="polite">
                  {scaleFactor && (s.scaleMode === 'final_size' || s.scaleMode === 'drawing_scale')
                    ? t('settings.scale.resultRatio', {
                        scale: scaleLabel,
                        extra:
                          s.scaleMode === 'final_size'
                            ? ratioLabel(scaleFactor)
                            : `${fmt.number(scaleFactor * 100)} %`,
                      })
                    : t('settings.scale.result', { scale: scaleLabel })}
                </Text>
              )}
              <div>
                <Text size="xs" fw={500} id="rotate-label">
                  {t('settings.rotate.label')}
                </Text>
                <SegmentedControl
                  fullWidth
                  size="xs"
                  aria-labelledby="rotate-label"
                  value={String(s.rotate)}
                  onChange={(v) => onPatch({ rotate: Number(v) as Rotation })}
                  data={[0, 90, 180, 270].map((d) => ({ value: String(d), label: `${d}°` }))}
                />
              </div>
              <Select
                label={t('settings.box.label')}
                size="xs"
                allowDeselect={false}
                value={s.box}
                data={opts(t, 'settings.box', ['crop', 'trim', 'bleed', 'media'] as SourceBox[])}
                onChange={(v) => v && onPatch({ box: v as SourceBox })}
              />
              <Switch
                size="xs"
                label={t('settings.mirror')}
                checked={s.mirror}
                onChange={(e) => onPatch({ mirror: e.currentTarget.checked })}
              />
              {layers.length > 0 && (
                <Stack
                  gap={4}
                  data-testid="layers-panel"
                  role="group"
                  aria-label={t('settings.layers.title')}
                >
                  <Text size="xs" fw={500}>
                    {t('settings.layers.title')}
                  </Text>
                  {layers.map((layer) => (
                    <Switch
                      key={layer.xref}
                      size="xs"
                      label={layer.name || t('settings.layers.unnamed', { n: layer.xref })}
                      checked={layerVisible(layer, s)}
                      onChange={(e) => onPatch(toggleLayer(layer, s, e.currentTarget.checked))}
                    />
                  ))}
                </Stack>
              )}
              {pageCount > 1 && (
                <TextInput
                  label={t('settings.pages.label')}
                  description={t('settings.pages.hint')}
                  size="xs"
                  value={s.pages}
                  onChange={(e) => onPatch({ pages: e.currentTarget.value })}
                />
              )}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>

        <Accordion.Item value="tiling">
          <Accordion.Control>{t('settings.sections.tiling')}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="xs">
              <div>
                <Text size="xs" fw={500} id="join-label">
                  {t('settings.tiling.join')}
                </Text>
                <SegmentedControl
                  fullWidth
                  size="xs"
                  aria-labelledby="join-label"
                  value={s.join}
                  onChange={(v) => onPatch({ join: v as 'overlap' | 'butt' })}
                  data={opts(t, 'settings.tiling.joins', ['overlap', 'butt'])}
                />
              </div>
              <LengthInput
                label={t('settings.tiling.overlap')}
                valueMm={butt ? 0 : s.overlapMm}
                minMm={OVERLAP_LIMITS.min}
                maxMm={OVERLAP_LIMITS.max}
                disabled={butt}
                onChangeMm={(v) => onPatch({ overlapMm: v })}
              />
              <SimpleGrid cols={2}>
                <Select
                  label={t('settings.tiling.alignX')}
                  size="xs"
                  allowDeselect={false}
                  value={s.alignX}
                  data={opts(t, 'settings.tiling.align', ['start', 'center', 'end'])}
                  onChange={(v) => v && onPatch({ alignX: v as Align })}
                />
                <Select
                  label={t('settings.tiling.alignY')}
                  size="xs"
                  allowDeselect={false}
                  value={s.alignY}
                  data={opts(t, 'settings.tiling.align', ['start', 'center', 'end'])}
                  onChange={(v) => v && onPatch({ alignY: v as Align })}
                />
              </SimpleGrid>
              <Switch
                size="xs"
                label={t('settings.tiling.skipBlank')}
                checked={s.skipBlank}
                onChange={(e) => onPatch({ skipBlank: e.currentTarget.checked })}
              />
              <Select
                label={t('settings.tiling.sensitivity')}
                size="xs"
                allowDeselect={false}
                disabled={!s.skipBlank}
                value={s.blankSensitivity}
                data={opts(t, 'settings.tiling.sensitivities', ['strict', 'normal', 'tolerant'])}
                onChange={(v) => v && onPatch({ blankSensitivity: v as BlankSensitivity })}
              />
              <SimpleGrid cols={2}>
                <Select
                  label={t('settings.tiling.order')}
                  size="xs"
                  allowDeselect={false}
                  value={s.order}
                  data={opts(t, 'settings.tiling.orders', ['rows', 'columns'])}
                  onChange={(v) => v && onPatch({ order: v as SheetOrder })}
                />
                <Select
                  label={t('settings.tiling.naming')}
                  size="xs"
                  allowDeselect={false}
                  value={s.naming}
                  data={opts(t, 'settings.tiling.namings', [
                    'letter_number',
                    'number_number',
                    'sequential',
                  ])}
                  onChange={(v) => v && onPatch({ naming: v as NamingScheme })}
                />
              </SimpleGrid>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>

        <Accordion.Item value="marks">
          <Accordion.Control>{t('settings.sections.marks')}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="xs">
              {(
                [
                  ['cutLines', 'cutLines'],
                  ['outerCutLines', 'outerCutLines'],
                  ['alignmentMarks', 'alignmentMarks'],
                  ['joinDiamonds', 'joinDiamonds'],
                  ['labels', 'labels'],
                  ['neighborHints', 'neighborHints'],
                  ['ruler', 'ruler'],
                  ['glueShading', 'glueShading'],
                ] as const
              ).map(([key, label]) => (
                <Switch
                  key={key}
                  size="xs"
                  label={t(`settings.marks.${label}`)}
                  checked={s[key]}
                  onChange={(e) => onPatch({ [key]: e.currentTarget.checked })}
                />
              ))}
              <Switch
                size="xs"
                label={t('settings.marks.marksLayer')}
                checked={s.marksLayer}
                onChange={(e) => onPatch({ marksLayer: e.currentTarget.checked })}
              />
              <Select
                label={t('settings.marks.testSquare')}
                size="xs"
                allowDeselect={false}
                value={s.testSquare}
                data={opts(t, 'settings.marks.testSquares', [
                  'none',
                  'assembly_page',
                  'first_sheet',
                ])}
                onChange={(v) => v && onPatch({ testSquare: v as TestSquare })}
              />
              <Select
                label={t('settings.marks.assemblyPage')}
                size="xs"
                allowDeselect={false}
                value={s.assemblyPage}
                data={opts(t, 'settings.marks.assemblyPages', ['none', 'first', 'last'])}
                onChange={(v) => v && onPatch({ assemblyPage: v as AssemblyPage })}
              />
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>

        <Accordion.Item value="output">
          <Accordion.Control>{t('settings.sections.output')}</Accordion.Control>
          <Accordion.Panel>
            <TextInput
              label={t('settings.output.filename')}
              description={t('settings.output.filenameHint')}
              size="xs"
              value={s.filenameTemplate}
              placeholder={DEFAULT_FILENAME_TEMPLATE}
              onChange={(e) => onPatch({ filenameTemplate: e.currentTarget.value })}
            />
            <Switch
              mt="xs"
              size="xs"
              label={t('settings.output.rasterize')}
              description={t('settings.output.rasterizeHint')}
              data-testid="rasterize"
              checked={s.rasterize}
              onChange={(e) => onPatch({ rasterize: e.currentTarget.checked })}
            />
            <Select
              mt="xs"
              size="xs"
              label={t('settings.output.dpi')}
              data-testid="output-dpi"
              allowDeselect={false}
              disabled={!s.rasterize}
              value={String(s.outputDpi)}
              data={OUTPUT_DPIS.map((d) => ({ value: String(d), label: `${d} DPI` }))}
              onChange={(v) => v && onPatch({ outputDpi: Number(v) as OutputDpi })}
            />
            <NumberInput
              mt="xs"
              size="xs"
              label={t('settings.output.minLineWidth')}
              description={
                s.rasterize
                  ? t('settings.output.minLineWidthHint')
                  : t('settings.output.minLineWidthDisabled')
              }
              disabled={!s.rasterize}
              min={0.01}
              max={2}
              step={0.05}
              decimalScale={2}
              allowNegative={false}
              placeholder="-"
              value={s.minLineWidthMm ?? ''}
              onChange={(v) =>
                onPatch({ minLineWidthMm: typeof v === 'number' && v > 0 ? v : null })
              }
            />
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Stack>
  );
}
