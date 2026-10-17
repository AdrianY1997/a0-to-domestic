import { Button, Checkbox, Group, Loader, SegmentedControl, Text } from '@mantine/core';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TransformComponent, TransformWrapper } from 'react-zoom-pan-pinch';
import type { Plan, RegionMm } from '../../api/types';
import type { Vec } from '../../lib/tiling';
import { PlanOverlay, type OverlayToggles, type PageImage, type PreviewTool } from './PlanOverlay';

interface Props {
  plan: Plan | undefined;
  image?: PageImage;
  loading: boolean;
  onOpenSheet: (id: string) => void;
  tool?: PreviewTool;
  onToolChange?: (tool: PreviewTool) => void;
  onToggleSheet?: (id: string) => void;
  offset?: Vec;
  onOffsetChange?: (offset: Vec) => void;
  onRegionDrawn?: (region: RegionMm) => void;
}

/** Preview with zoom (wheel/pinch/buttons), pan (drag) and the overlay toggles (SPEC 8.2). */
export function PreviewPane({
  plan,
  image,
  loading,
  onOpenSheet,
  tool = 'pan',
  onToolChange,
  onToggleSheet,
  offset,
  onOffsetChange,
  onRegionDrawn,
}: Props) {
  const { t } = useTranslation();
  const [toggles, setToggles] = useState<OverlayToggles>({
    margins: true,
    overlaps: true,
    ids: true,
  });
  const set = (k: keyof OverlayToggles) => (v: boolean) => setToggles((s) => ({ ...s, [k]: v }));

  return (
    <>
      <div className="preview-canvas" aria-busy={loading}>
        <TransformWrapper
          minScale={0.5}
          maxScale={20}
          doubleClick={{ disabled: true }}
          wheel={{ step: 0.15 }}
          centerOnInit
          panning={{ disabled: tool !== 'pan' }}
        >
          {({ zoomIn, zoomOut, resetTransform }) => (
            <>
              <TransformComponent
                wrapperStyle={{ width: '100%', height: '100%' }}
                contentStyle={{ width: '100%', height: '100%' }}
              >
                {plan ? (
                  <PlanOverlay
                    plan={plan}
                    image={image}
                    toggles={toggles}
                    onOpenSheet={onOpenSheet}
                    tool={tool}
                    onToggleSheet={onToggleSheet}
                    offset={offset}
                    onOffsetChange={onOffsetChange}
                    onRegionDrawn={onRegionDrawn}
                  />
                ) : (
                  <Group h="100%" justify="center">
                    <Loader size="sm" />
                  </Group>
                )}
              </TransformComponent>
              <Group
                gap={4}
                p={6}
                style={{ position: 'absolute', top: 0, left: 0, zIndex: 2 }}
                role="toolbar"
                aria-label={t('editor.zoom.label')}
              >
                <Button
                  size="compact-sm"
                  variant="default"
                  onClick={() => zoomOut()}
                  aria-label={t('editor.zoom.out')}
                >
                  −
                </Button>
                <Button
                  size="compact-sm"
                  variant="default"
                  onClick={() => zoomIn()}
                  aria-label={t('editor.zoom.in')}
                >
                  +
                </Button>
                <Button size="compact-sm" variant="default" onClick={() => resetTransform()}>
                  {t('editor.zoom.fit')}
                </Button>
              </Group>
            </>
          )}
        </TransformWrapper>
      </div>
      {onToolChange && (
        <Group gap="xs" mt={6} align="center">
          <Text size="xs" fw={500} id="preview-tool-label">
            {t('editor.tool.label')}
          </Text>
          <SegmentedControl
            size="xs"
            aria-labelledby="preview-tool-label"
            data-testid="preview-tool"
            value={tool}
            onChange={(v) => onToolChange(v as PreviewTool)}
            data={(['pan', 'grid', 'region'] as const).map((v) => ({
              value: v,
              label: t(`editor.tool.${v}`),
            }))}
          />
          <Text size="xs" c="dimmed">
            {t(`editor.tool.hint.${tool}`)}
          </Text>
        </Group>
      )}
      <Group gap="md" mt={6} justify="space-between">
        <Group gap="md" role="group" aria-label={t('editor.overlay.toggles')}>
          <Checkbox
            size="xs"
            label={t('editor.overlay.margins')}
            checked={toggles.margins}
            onChange={(e) => set('margins')(e.currentTarget.checked)}
          />
          <Checkbox
            size="xs"
            label={t('editor.overlay.overlaps')}
            checked={toggles.overlaps}
            onChange={(e) => set('overlaps')(e.currentTarget.checked)}
          />
          <Checkbox
            size="xs"
            label={t('editor.overlay.ids')}
            checked={toggles.ids}
            onChange={(e) => set('ids')(e.currentTarget.checked)}
          />
        </Group>
        <Text size="xs" c="dimmed">
          {t('editor.overlay.hint')}
        </Text>
      </Group>
    </>
  );
}
