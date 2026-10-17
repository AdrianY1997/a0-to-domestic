/**
 * Editor settings (the UI model) and their mapping to the API's `JobSettings` (SPEC Appendix B).
 * Lengths are always millimetres here; unit conversion happens only at the UI edge.
 */
import type {
  Align,
  AssemblyPage,
  BlankSensitivity,
  JobSettings,
  JoinMethod,
  Margins,
  NamingScheme,
  Orientation,
  PaperId,
  RegionMm,
  Rotation,
  SheetOrder,
  SourceBox,
  TestSquare,
} from '../api/types';

export type ScaleMode =
  'percent' | 'fit_sheet' | 'fit_grid' | 'fit_count' | 'final_size' | 'drawing_scale';

export const SCALE_MODES: readonly ScaleMode[] = [
  'percent',
  'fit_sheet',
  'fit_grid',
  'fit_count',
  'final_size',
  'drawing_scale',
];

export interface EditorSettings {
  // source
  pages: string;
  box: SourceBox;
  rotate: Rotation;
  /** Region of the view (view-space mm) to print; null = whole page. */
  regionMm: RegionMm | null;
  autoTrim: boolean;
  /** Horizontal mirror of the source (ESC-06). */
  mirror: boolean;
  /** Source layers (OCG xrefs as strings) explicitly turned off in the output (ANA-07). */
  hiddenLayers: string[];
  /** Layers that are OFF in the source and explicitly turned on in the output (ANA-07). */
  visibleLayers: string[];
  // target
  printerId: string;
  paper: PaperId;
  customPaperWidthMm: number;
  customPaperHeightMm: number;
  /** Roll / banner paper (PAP-06): replaces `paper`; sheets are cut to the needed length. */
  banner: boolean;
  rollWidthMm: number;
  rollMaxLengthMm: number;
  orientation: Orientation;
  customMargins: boolean;
  marginsMm: Margins;
  // scale
  scaleMode: ScaleMode;
  percent: number;
  fitCols: number;
  fitRows: number;
  maxSheets: number;
  finalDimension: 'width' | 'height';
  finalSizeMm: number;
  drawingFrom: number;
  drawingTo: number;
  // tiling
  join: JoinMethod;
  overlapMm: number;
  alignX: Align;
  alignY: Align;
  order: SheetOrder;
  naming: NamingScheme;
  skipBlank: boolean;
  blankSensitivity: BlankSensitivity;
  /** Grid offset in mm (dragged on the preview), clamped by the plan's `offset_range_mm`. */
  offsetXMm: number;
  offsetYMm: number;
  /** Sheet IDs to print; null = all ("reprint sheets"). */
  onlySheets: string[] | null;
  // marks
  cutLines: boolean;
  outerCutLines: boolean;
  alignmentMarks: boolean;
  joinDiamonds: boolean;
  labels: boolean;
  neighborHints: boolean;
  ruler: boolean;
  glueShading: boolean;
  testSquare: TestSquare;
  assemblyPage: AssemblyPage;
  /** Draw the marks in a PDF layer that viewers can hide (MAR-05). */
  marksLayer: boolean;
  // colour
  /** Minimum stroke width in mm for rasterized output (COL-06); null = off. */
  minLineWidthMm: number | null;
  /** Rasterize the output (SAL-04) at `outputDpi`. */
  rasterize: boolean;
  outputDpi: OutputDpi;
  // output
  filenameTemplate: string;
}

export const DEFAULT_FILENAME_TEMPLATE = '{name}_{paper}-{orientation}_{cols}x{rows}_{scale}pct';

export const DEFAULT_EDITOR_SETTINGS: EditorSettings = {
  pages: 'all',
  box: 'crop',
  rotate: 0,
  regionMm: null,
  autoTrim: false,
  mirror: false,
  hiddenLayers: [],
  visibleLayers: [],
  printerId: 'builtin-generic',
  paper: 'A4',
  customPaperWidthMm: 210,
  customPaperHeightMm: 297,
  banner: false,
  rollWidthMm: 610,
  rollMaxLengthMm: 3000,
  orientation: 'auto',
  customMargins: false,
  marginsMm: { top: 5, right: 5, bottom: 5, left: 5 },
  scaleMode: 'percent',
  percent: 100,
  fitCols: 2,
  fitRows: 2,
  maxSheets: 4,
  finalDimension: 'width',
  finalSizeMm: 1000,
  drawingFrom: 50,
  drawingTo: 100,
  join: 'overlap',
  overlapMm: 10,
  alignX: 'center',
  alignY: 'center',
  order: 'rows',
  naming: 'letter_number',
  skipBlank: true,
  blankSensitivity: 'normal',
  offsetXMm: 0,
  offsetYMm: 0,
  onlySheets: null,
  cutLines: true,
  outerCutLines: false,
  alignmentMarks: true,
  joinDiamonds: true,
  labels: true,
  neighborHints: true,
  ruler: true,
  glueShading: false,
  testSquare: 'assembly_page',
  assemblyPage: 'first',
  marksLayer: false,
  minLineWidthMm: null,
  rasterize: false,
  outputDpi: 300,
  filenameTemplate: DEFAULT_FILENAME_TEMPLATE,
};

export const PAPER_CUSTOM_LIMITS = { minW: 50, maxW: 500, minH: 50, maxH: 1500 } as const;
export const ROLL_LIMITS = { minW: 50, maxW: 1500, minL: 100, maxL: 30000 } as const;
export const OVERLAP_LIMITS = { min: 3, max: 50 } as const;

function scaleToApi(s: EditorSettings): NonNullable<JobSettings['scale']> {
  switch (s.scaleMode) {
    case 'percent':
      return { mode: 'percent', percent: s.percent };
    case 'fit_sheet':
      return { mode: 'fit_sheet' };
    case 'fit_grid':
      return { mode: 'fit_grid', cols: s.fitCols, rows: s.fitRows };
    case 'fit_count':
      return { mode: 'fit_count', max_sheets: s.maxSheets };
    case 'final_size':
      // Exactly one of width/height (the backend rejects both).
      return s.finalDimension === 'width'
        ? { mode: 'final_size', width_mm: s.finalSizeMm }
        : { mode: 'final_size', height_mm: s.finalSizeMm };
    case 'drawing_scale':
      return {
        mode: 'drawing_scale',
        from_denominator: s.drawingFrom,
        to_denominator: s.drawingTo,
      };
  }
}

/** Editor settings -> the `JobSettings` sent to `/plan` and `/jobs`. */
export function toJobSettings(documentId: string, s: EditorSettings): JobSettings {
  const butt = s.join === 'butt';
  return {
    schema_version: 1,
    document_id: documentId,
    source: {
      pages: s.pages.trim() === '' ? 'all' : s.pages.trim(),
      box: s.box,
      rotate: s.rotate,
      mirror: s.mirror,
      region_mm: s.regionMm ? { ...s.regionMm } : null,
      auto_trim: s.autoTrim,
      hidden_layers: [...s.hiddenLayers],
      visible_layers: [...s.visibleLayers],
    },
    target: {
      printer_id: s.printerId,
      paper: s.paper,
      custom_paper_mm:
        s.paper === 'CUSTOM'
          ? { width: s.customPaperWidthMm, height: s.customPaperHeightMm }
          : null,
      roll_mm: s.banner ? { width: s.rollWidthMm, max_length: s.rollMaxLengthMm } : null,
      orientation: s.orientation,
      margins_mm: s.customMargins ? { ...s.marginsMm } : null,
      info_band: { position: 'none', height_mm: 0 },
      landscape_as_rotated_portrait: false,
    },
    scale: scaleToApi(s),
    tiling: {
      join: s.join,
      overlap_mm: butt ? 0 : s.overlapMm,
      align: { x: s.alignX, y: s.alignY },
      offset_mm: { x: s.offsetXMm, y: s.offsetYMm },
      order: s.order,
      naming: s.naming,
      skip_blank: s.skipBlank,
      blank_sensitivity: s.blankSensitivity,
      only_sheets: s.onlySheets ? [...s.onlySheets] : null,
    },
    marks: {
      cut_lines: s.cutLines,
      outer_cut_lines: s.outerCutLines,
      alignment_marks: s.alignmentMarks,
      join_diamonds: s.joinDiamonds,
      labels: s.labels,
      neighbor_hints: s.neighborHints,
      ruler: s.ruler,
      glue_shading: s.glueShading,
      test_square: s.testSquare,
      assembly_page: s.assemblyPage,
      color: '#808080',
      line_width_pt: 0.5,
      marks_layer: s.marksLayer,
    },
    color: {
      mode: 'color',
      threshold: 160,
      invert: false,
      whiten_background: false,
      min_line_width_mm: s.minLineWidthMm,
    },
    output: {
      formats: ['pdf'],
      rasterize: s.rasterize,
      dpi: s.outputDpi,
      max_dpi: 600,
      jpeg_quality: 90,
      filename_template: s.filenameTemplate.trim() || DEFAULT_FILENAME_TEMPLATE,
      include_reduced_copy: false,
    },
  };
}

// ----------------------------------------------------------------------------- layers

/** A source layer as the API reports it (`on` = its state in the file). */
export interface LayerState {
  xref: number;
  on: boolean;
}

type LayerFields = Pick<EditorSettings, 'hiddenLayers' | 'visibleLayers'>;

/**
 * Effective visibility of a layer in the output (ANA-07): explicitly hidden wins, then
 * explicitly visible, else the state in the source file. Mirrors the backend rule
 * `off = (source_off - visible) | hidden`, so the panel, the preview and the output agree.
 */
export function layerVisible(layer: LayerState, s: LayerFields): boolean {
  const id = String(layer.xref);
  if (s.hiddenLayers.includes(id)) return false;
  if (s.visibleLayers.includes(id)) return true;
  return layer.on;
}

/** Settings patch that makes `layer` visible (`on`) or hidden, keeping both lists minimal. */
export function toggleLayer(layer: LayerState, s: LayerFields, on: boolean): LayerFields {
  const id = String(layer.xref);
  const hidden = s.hiddenLayers.filter((x) => x !== id);
  const visible = s.visibleLayers.filter((x) => x !== id);
  if (on) return { hiddenLayers: hidden, visibleLayers: layer.on ? visible : [...visible, id] };
  return { hiddenLayers: layer.on ? [...hidden, id] : hidden, visibleLayers: visible };
}

// ---------------------------------------------------------------------------- presets

type Dict = Record<string, unknown>;

function isDict(x: unknown): x is Dict {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

function pick<T>(value: unknown, allowed: readonly T[]): T | undefined {
  return allowed.includes(value as T) ? (value as T) : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function bool(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function defined<T extends object>(obj: T): Partial<T> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as Partial<T>;
}

const BOXES: readonly SourceBox[] = ['crop', 'trim', 'bleed', 'media'];
const ROTATIONS: readonly Rotation[] = [0, 90, 180, 270];
const ORIENTATIONS: readonly Orientation[] = ['auto', 'portrait', 'landscape'];
export const OUTPUT_DPIS = [150, 200, 300, 600] as const;
export type OutputDpi = (typeof OUTPUT_DPIS)[number];
const JOINS: readonly JoinMethod[] = ['overlap', 'butt'];
const ALIGNS: readonly Align[] = ['start', 'center', 'end'];
const ORDERS: readonly SheetOrder[] = ['rows', 'columns'];
const NAMINGS: readonly NamingScheme[] = ['letter_number', 'number_number', 'sequential'];
const SENSITIVITIES: readonly BlankSensitivity[] = ['strict', 'normal', 'tolerant'];
const TEST_SQUARES: readonly TestSquare[] = ['none', 'assembly_page', 'first_sheet'];
const ASSEMBLY_PAGES: readonly AssemblyPage[] = ['none', 'first', 'last'];

/**
 * Merges a partial `JobSettings` (a preset, SPEC 11.3) over the editor settings. Unknown or
 * invalid fields are ignored.
 */
export function mergeJobSettingsPatch(base: EditorSettings, patch: unknown): EditorSettings {
  if (!isDict(patch)) return base;
  const out: EditorSettings = { ...base };
  const source = isDict(patch.source) ? patch.source : {};
  const target = isDict(patch.target) ? patch.target : {};
  const scale = isDict(patch.scale) ? patch.scale : null;
  const tiling = isDict(patch.tiling) ? patch.tiling : {};
  const marks = isDict(patch.marks) ? patch.marks : {};
  const output = isDict(patch.output) ? patch.output : {};

  Object.assign(
    out,
    defined({
      pages: str(source.pages),
      autoTrim: bool(source.auto_trim),
      mirror: bool(source.mirror),
      marksLayer: bool(marks.marks_layer),
      box: pick(source.box, BOXES),
      rotate: pick(source.rotate, ROTATIONS),
      printerId: str(target.printer_id),
      paper: str(target.paper) as PaperId | undefined,
      orientation: pick(target.orientation, ORIENTATIONS),
      join: pick(tiling.join, JOINS),
      overlapMm: num(tiling.overlap_mm),
      alignX: isDict(tiling.align) ? pick(tiling.align.x, ALIGNS) : undefined,
      alignY: isDict(tiling.align) ? pick(tiling.align.y, ALIGNS) : undefined,
      order: pick(tiling.order, ORDERS),
      naming: pick(tiling.naming, NAMINGS),
      skipBlank: bool(tiling.skip_blank),
      blankSensitivity: pick(tiling.blank_sensitivity, SENSITIVITIES),
      cutLines: bool(marks.cut_lines),
      outerCutLines: bool(marks.outer_cut_lines),
      alignmentMarks: bool(marks.alignment_marks),
      joinDiamonds: bool(marks.join_diamonds),
      labels: bool(marks.labels),
      neighborHints: bool(marks.neighbor_hints),
      ruler: bool(marks.ruler),
      glueShading: bool(marks.glue_shading),
      testSquare: pick(marks.test_square, TEST_SQUARES),
      assemblyPage: pick(marks.assembly_page, ASSEMBLY_PAGES),
      filenameTemplate: str(output.filename_template),
      rasterize: bool(output.rasterize),
      outputDpi: pick(output.dpi, OUTPUT_DPIS),
    }),
  );

  if (Array.isArray(source.hidden_layers)) {
    out.hiddenLayers = source.hidden_layers.filter((x): x is string => typeof x === 'string');
  }
  if (Array.isArray(source.visible_layers)) {
    out.visibleLayers = source.visible_layers.filter((x): x is string => typeof x === 'string');
  }
  if (isDict(patch.color) && patch.color.min_line_width_mm !== undefined) {
    out.minLineWidthMm = num(patch.color.min_line_width_mm) ?? null;
  }

  if (isDict(target.custom_paper_mm)) {
    out.customPaperWidthMm = num(target.custom_paper_mm.width) ?? out.customPaperWidthMm;
    out.customPaperHeightMm = num(target.custom_paper_mm.height) ?? out.customPaperHeightMm;
  }
  if (isDict(target.roll_mm)) {
    out.banner = true;
    out.rollWidthMm = num(target.roll_mm.width) ?? out.rollWidthMm;
    out.rollMaxLengthMm = num(target.roll_mm.max_length) ?? out.rollMaxLengthMm;
  } else if (target.roll_mm === null || target.paper !== undefined) {
    // a preset that names a regular paper (or clears the roll) leaves banner mode
    out.banner = false;
  }
  if (target.margins_mm === null) out.customMargins = false;
  if (isDict(target.margins_mm)) {
    out.customMargins = true;
    out.marginsMm = {
      top: num(target.margins_mm.top) ?? out.marginsMm.top,
      right: num(target.margins_mm.right) ?? out.marginsMm.right,
      bottom: num(target.margins_mm.bottom) ?? out.marginsMm.bottom,
      left: num(target.margins_mm.left) ?? out.marginsMm.left,
    };
  }

  if (scale) {
    const mode = pick(scale.mode, SCALE_MODES);
    if (mode) {
      out.scaleMode = mode;
      if (mode === 'percent') out.percent = num(scale.percent) ?? 100;
      if (mode === 'fit_grid') {
        out.fitCols = num(scale.cols) ?? out.fitCols;
        out.fitRows = num(scale.rows) ?? out.fitRows;
      }
      if (mode === 'fit_count') out.maxSheets = num(scale.max_sheets) ?? out.maxSheets;
      if (mode === 'final_size') {
        const w = num(scale.width_mm);
        const h = num(scale.height_mm);
        if (w !== undefined) {
          out.finalDimension = 'width';
          out.finalSizeMm = w;
        } else if (h !== undefined) {
          out.finalDimension = 'height';
          out.finalSizeMm = h;
        }
      }
      if (mode === 'drawing_scale') {
        out.drawingFrom = num(scale.from_denominator) ?? out.drawingFrom;
        out.drawingTo = num(scale.to_denominator) ?? out.drawingTo;
      }
    }
  }

  // The editor keeps the overlap value while `butt` is selected (it is sent as 0), so switching
  // back to `overlap` restores it; a 0 coming from a preset falls back to the default.
  if (out.overlapMm < OVERLAP_LIMITS.min) out.overlapMm = DEFAULT_EDITOR_SETTINGS.overlapMm;
  return out;
}

/** Keys of `EditorSettings` that change the scale (and so the preset's "exact scale" meaning). */
export const SCALE_KEYS: readonly (keyof EditorSettings)[] = [
  'scaleMode',
  'percent',
  'fitCols',
  'fitRows',
  'maxSheets',
  'finalDimension',
  'finalSizeMm',
  'drawingFrom',
  'drawingTo',
];
