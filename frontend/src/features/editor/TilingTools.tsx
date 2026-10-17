import { Button, Group, SimpleGrid, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Plan } from '../../api/types';
import { LengthInput } from '../../components/LengthInput';
import type { EditorSettings } from '../../lib/settings';
import {
  clamp,
  formatSheetSelection,
  normalizeOnlySheets,
  parseSheetSelection,
  patchRegion,
  selectedIds,
  type RegionMm,
} from '../../lib/tiling';
import type { PreviewTool } from './PlanOverlay';

interface Props {
  settings: EditorSettings;
  plan: Plan | undefined;
  onPatch: (patch: Partial<EditorSettings>) => void;
  onToolChange: (tool: PreviewTool) => void;
}

/** F2 tiling tools (SPEC 8.2): reprint sheets, grid offset, region and auto-trim. */
export function TilingTools({ settings: s, plan, onPatch, onToolChange }: Props) {
  const { t } = useTranslation();
  const sheets = plan?.sheets;
  const selection = useMemo(
    () => (plan && s.onlySheets ? formatSheetSelection(selectedIds(plan), plan.sheets) : ''),
    [plan, s.onlySheets],
  );
  const [text, setText] = useState(selection);
  const [focused, setFocused] = useState(false);
  const [invalid, setInvalid] = useState<string[]>([]);
  useEffect(() => {
    if (!focused) {
      setText(selection);
      setInvalid([]);
    }
  }, [selection, focused]);

  const applyText = (value: string) => {
    setText(value);
    if (!sheets) return;
    if (value.trim() === '') {
      setInvalid([]);
      onPatch({ onlySheets: null });
      return;
    }
    const parsed = parseSheetSelection(value, sheets);
    setInvalid(parsed.invalid);
    if (parsed.invalid.length === 0) {
      onPatch({ onlySheets: normalizeOnlySheets(parsed.ids, sheets) });
    }
  };

  const range = plan?.offset_range_mm;
  const canOffset = !!range && (range.x[1] > range.x[0] || range.y[1] > range.y[0]);
  const viewSize = plan?.view_size_mm;
  const region = s.regionMm;
  const setRegion = (field: keyof RegionMm) => (mm: number) => {
    if (region && viewSize) onPatch({ regionMm: patchRegion(region, field, mm, viewSize) });
  };
  const total = plan?.counts.total ?? 0;
  const chosen = plan ? plan.sheets.filter((x) => x.selected).length : 0;

  return (
    <Stack
      gap="sm"
      component="section"
      aria-label={t('editor.tools.title')}
      data-testid="tiling-tools"
    >
      <Title order={2} size="h5">
        {t('editor.tools.title')}
      </Title>

      <Stack gap={4}>
        <TextInput
          label={t('editor.tools.sheets.label')}
          description={t('editor.tools.sheets.hint')}
          placeholder={t('editor.tools.sheets.placeholder')}
          size="xs"
          data-testid="only-sheets"
          value={text}
          disabled={!plan}
          error={
            invalid.length > 0
              ? t('editor.tools.sheets.invalid', { ids: invalid.join(', ') })
              : undefined
          }
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(e) => applyText(e.currentTarget.value)}
        />
        <Group gap="xs" justify="space-between">
          <Text size="xs" c="dimmed" aria-live="polite">
            {t('editor.tools.sheets.count', { chosen, total })}
          </Text>
          <Group gap={4}>
            <Button
              size="compact-xs"
              variant="default"
              disabled={!plan || s.onlySheets === null}
              onClick={() => onPatch({ onlySheets: null })}
            >
              {t('editor.tools.sheets.all')}
            </Button>
            <Button
              size="compact-xs"
              variant="default"
              disabled={!plan}
              onClick={() => onPatch({ onlySheets: [] })}
            >
              {t('editor.tools.sheets.none')}
            </Button>
          </Group>
        </Group>
      </Stack>

      <Stack gap={4}>
        <Text size="xs" fw={500}>
          {t('editor.tools.offset.label')}
        </Text>
        <SimpleGrid cols={2}>
          {(['x', 'y'] as const).map((axis) => {
            const [lo, hi] = range ? range[axis] : [0, 0];
            const raw = axis === 'x' ? s.offsetXMm : s.offsetYMm;
            return (
              <LengthInput
                key={axis}
                label={t(`editor.tools.offset.${axis}`)}
                valueMm={clamp(raw, lo, hi)}
                minMm={lo}
                maxMm={hi}
                disabled={!canOffset || hi <= lo}
                onChangeMm={(v) =>
                  onPatch(
                    axis === 'x' ? { offsetXMm: clamp(v, lo, hi) } : { offsetYMm: clamp(v, lo, hi) },
                  )
                }
              />
            );
          })}
        </SimpleGrid>
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {t('editor.tools.offset.hint')}
          </Text>
          <Button
            size="compact-xs"
            variant="default"
            disabled={s.offsetXMm === 0 && s.offsetYMm === 0}
            onClick={() => onPatch({ offsetXMm: 0, offsetYMm: 0 })}
          >
            {t('editor.tools.offset.reset')}
          </Button>
        </Group>
      </Stack>

      <Stack gap={4}>
        <Text size="xs" fw={500}>
          {t('editor.tools.region.label')}
        </Text>
        {region && viewSize ? (
          <SimpleGrid cols={2} data-testid="region-inputs">
            {(['x', 'y', 'width', 'height'] as const).map((f) => (
              <LengthInput
                key={f}
                label={t(`editor.tools.region.${f}`)}
                valueMm={region[f]}
                minMm={f === 'width' || f === 'height' ? 1 : 0}
                maxMm={f === 'x' || f === 'width' ? viewSize[0] : viewSize[1]}
                onChangeMm={setRegion(f)}
              />
            ))}
          </SimpleGrid>
        ) : (
          <Text size="xs" c="dimmed">
            {t('editor.tools.region.none')}
          </Text>
        )}
        <Group gap="xs">
          <Button size="compact-xs" variant="default" onClick={() => onToolChange('region')}>
            {t('editor.tools.region.draw')}
          </Button>
          <Button
            size="compact-xs"
            variant="default"
            disabled={!region}
            data-testid="region-clear"
            onClick={() => onPatch({ regionMm: null })}
          >
            {t('editor.tools.region.clear')}
          </Button>
        </Group>
      </Stack>

      <Switch
        size="xs"
        label={t('editor.tools.autoTrim.label')}
        description={t('editor.tools.autoTrim.hint')}
        checked={s.autoTrim}
        data-testid="auto-trim"
        onChange={(e) => onPatch({ autoTrim: e.currentTarget.checked })}
      />
    </Stack>
  );
}
