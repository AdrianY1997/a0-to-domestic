import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Plan, RegionMm, Rotation } from '../../api/types';
import {
  buildOverlay,
  canonicalToViewMatrix,
  matrixToCss,
  type SheetGeometry,
} from '../../lib/geometry';
import { clientToViewMm, dragOffset, regionFromDrag, type Vec } from '../../lib/tiling';

/** What a drag on the preview does: pan/zoom, move the grid (offset) or draw a region. */
export type PreviewTool = 'pan' | 'grid' | 'region';

/** Pointer movement (px) below which a press still counts as a click on a sheet. */
const CLICK_SLOP_PX = 4;

export interface OverlayToggles {
  margins: boolean;
  overlaps: boolean;
  ids: boolean;
}

export interface PageImage {
  url: string;
  /** Canonical page size in mm (the preview image covers all of it). */
  widthMm: number;
  heightMm: number;
  /** Selected box in the canonical frame: [x0, y0, x1, y1]. */
  box: readonly [number, number, number, number];
  rotate: Rotation;
  /** Horizontal mirror of the view (ESC-06). */
  mirror?: boolean;
}

interface Props {
  plan: Plan;
  image?: PageImage;
  toggles: OverlayToggles;
  onOpenSheet?: (sheetId: string) => void;
  tool?: PreviewTool;
  /** Click on a sheet: include/exclude it. */
  onToggleSheet?: (sheetId: string) => void;
  /** Current grid offset (mm); a drag in the `grid` tool reports a new one. */
  offset?: Vec;
  onOffsetChange?: (offset: Vec) => void;
  /** A rectangle drawn in the `region` tool, in view-space mm. */
  onRegionDrawn?: (region: RegionMm) => void;
}

/**
 * The preview layer: the page image plus an SVG whose viewBox is in view-space mm (SPEC 8.2).
 * Tiles, glue strips, cut lines, IDs and dimmed (blank/deselected) sheets are drawn from the plan.
 */
export function PlanOverlay({
  plan,
  image,
  toggles,
  onOpenSheet,
  tool = 'pan',
  onToggleSheet,
  offset = { x: 0, y: 0 },
  onOffsetChange,
  onRegionDrawn,
}: Props) {
  const { t } = useTranslation();
  const geo = useMemo(() => buildOverlay(plan), [plan]);
  const { viewBox, view } = geo;
  const matrix = image
    ? matrixToCss(canonicalToViewMatrix(image.box, image.rotate, image.mirror))
    : undefined;

  const svgRef = useRef<SVGSVGElement>(null);
  const downRef = useRef<{ x: number; y: number } | null>(null);
  const movedRef = useRef(false);
  // Live feedback while dragging: grid shift (view mm) or the rubber-band rectangle.
  const [shift, setShift] = useState<Vec | null>(null);
  const [rubber, setRubber] = useState<RegionMm | null>(null);
  const cleanup = useRef<(() => void) | null>(null);
  useEffect(() => () => cleanup.current?.(), []);

  const range = plan.offset_range_mm;
  const canDrag = range.x[1] > range.x[0] || range.y[1] > range.y[0];
  const toView = (cx: number, cy: number): Vec =>
    clientToViewMm(svgRef.current!.getBoundingClientRect(), viewBox, cx, cy);

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    downRef.current = { x: e.clientX, y: e.clientY };
    movedRef.current = false;
    if (e.button !== 0 || tool === 'pan' || !svgRef.current) return;
    if (tool === 'grid' && !canDrag) return;
    e.preventDefault();
    const start = toView(e.clientX, e.clientY);
    const startOffset = offset;
    let last: Vec = start;
    const onMove = (ev: PointerEvent) => {
      const d = downRef.current;
      if (d && Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > CLICK_SLOP_PX)
        movedRef.current = true;
      last = toView(ev.clientX, ev.clientY);
      if (tool === 'grid') {
        const next = dragOffset(
          startOffset,
          { x: last.x - start.x, y: last.y - start.y },
          plan.scale,
          range,
        );
        setShift({
          x: (next.x - startOffset.x) / plan.scale,
          y: (next.y - startOffset.y) / plan.scale,
        });
      } else {
        setRubber(regionFromDrag(start, last, plan.view_size_mm));
      }
    };
    const finish = (commit: boolean) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      cleanup.current = null;
      setShift(null);
      setRubber(null);
      if (!commit || !movedRef.current) return;
      if (tool === 'grid') {
        const next = dragOffset(
          startOffset,
          { x: last.x - start.x, y: last.y - start.y },
          plan.scale,
          range,
        );
        if (next.x !== startOffset.x || next.y !== startOffset.y) onOffsetChange?.(next);
      } else {
        const region = regionFromDrag(start, last, plan.view_size_mm);
        if (region) onRegionDrawn?.(region);
      }
    };
    const onUp = () => finish(true);
    const onCancel = () => finish(false);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
    cleanup.current = () => finish(false);
  };

  // In pan mode the library pans; a press that travelled is not a click.
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = downRef.current;
    if (d && e.buttons && Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_SLOP_PX) {
      movedRef.current = true;
    }
  };
  const sheetClick = (id: string) => {
    if (!movedRef.current) onToggleSheet?.(id);
    movedRef.current = false;
  };

  return (
    <svg
      ref={svgRef}
      className={`plan-overlay plan-overlay--${tool}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
      preserveAspectRatio="xMidYMid meet"
      role="group"
      aria-label={t('editor.overlay.label', {
        sheets: plan.counts.printed,
        cols: plan.grid.cols,
        rows: plan.grid.rows,
      })}
      data-testid="plan-overlay"
    >
      <defs>
        <clipPath id="plan-view-clip">
          <rect x={view.x} y={view.y} width={view.w} height={view.h} />
        </clipPath>
      </defs>
      <rect
        x={view.x}
        y={view.y}
        width={view.w}
        height={view.h}
        className="plan-page"
        data-testid="plan-page"
      />
      {image && (
        <g clipPath="url(#plan-view-clip)" data-testid="plan-image-clip">
          <image
            href={image.url}
            x={0}
            y={0}
            width={image.widthMm}
            height={image.heightMm}
            transform={matrix}
            preserveAspectRatio="none"
            data-testid="plan-image"
          />
        </g>
      )}
      <g
        transform={shift ? `translate(${shift.x} ${shift.y})` : undefined}
        data-testid="plan-sheets"
      >
        {geo.sheets.map((sheet) => (
          <SheetShape
            key={sheet.id}
            sheet={sheet}
            toggles={toggles}
            fontSize={geo.fontSize}
            onOpen={onOpenSheet}
            onToggle={sheetClick}
          />
        ))}
      </g>
      {rubber && (
        <rect
          x={rubber.x}
          y={rubber.y}
          width={rubber.width}
          height={rubber.height}
          className="plan-region"
          vectorEffect="non-scaling-stroke"
          data-testid="region-rubber"
        />
      )}
    </svg>
  );
}

function SheetShape({
  sheet,
  toggles,
  fontSize,
  onOpen,
  onToggle,
}: {
  sheet: SheetGeometry;
  toggles: OverlayToggles;
  fontSize: number;
  onOpen?: (id: string) => void;
  onToggle?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const { content, paper } = sheet;
  const stateLabel = t(`editor.sheetState.${sheet.state}`);
  return (
    <g
      className={`plan-sheet plan-sheet--${sheet.state}`}
      data-testid={`sheet-${sheet.id}`}
      data-state={sheet.state}
      role="button"
      tabIndex={0}
      aria-label={t('editor.sheetAria', { id: sheet.id, state: stateLabel })}
      aria-pressed={sheet.state !== 'deselected'}
      onClick={(e) => {
        if (e.detail > 1) return; // second click of a double-click opens the sheet only
        onToggle?.(sheet.id);
      }}
      onDoubleClick={() => onOpen?.(sheet.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          onOpen?.(sheet.id);
        } else if (e.key === ' ') {
          e.preventDefault();
          onToggle?.(sheet.id);
        }
      }}
    >
      <title>{t('editor.sheetTitle', { id: sheet.id })}</title>
      {toggles.margins && (
        <rect
          x={paper.x}
          y={paper.y}
          width={paper.w}
          height={paper.h}
          className="plan-paper"
          vectorEffect="non-scaling-stroke"
        />
      )}
      <rect
        x={content.x}
        y={content.y}
        width={content.w}
        height={content.h}
        className="plan-content"
        vectorEffect="non-scaling-stroke"
      />
      {toggles.overlaps &&
        sheet.overlaps.map((r, i) => (
          <rect
            key={i}
            x={r.x}
            y={r.y}
            width={r.w}
            height={r.h}
            className="plan-overlap"
            data-testid="overlap-strip"
          />
        ))}
      {sheet.cutLines.map(([x1, y1, x2, y2], i) => (
        <line
          key={i}
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          className="plan-cut"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {sheet.dimmed && (
        <>
          <line
            x1={content.x}
            y1={content.y}
            x2={content.x + content.w}
            y2={content.y + content.h}
            className="plan-skip"
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={content.x + content.w}
            y1={content.y}
            x2={content.x}
            y2={content.y + content.h}
            className="plan-skip"
            vectorEffect="non-scaling-stroke"
          />
        </>
      )}
      {toggles.ids && (
        <text
          x={sheet.label.x}
          y={sheet.label.y}
          fontSize={fontSize}
          textAnchor="middle"
          dominantBaseline="central"
          className="plan-id"
        >
          {sheet.id}
        </text>
      )}
    </g>
  );
}
