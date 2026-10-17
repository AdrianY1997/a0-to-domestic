import type { components } from './schema';

type S = components['schemas'];

export type Plan = S['Plan'];
export type SheetPlan = S['SheetPlan'];
export type PlanWarning = S['PlanWarning'];
export type WarningCode = S['WarningCode'];
export type PlanRequest = S['PlanRequest'];
export type SheetPreviewRequest = S['SheetPreviewRequest'];
export type JobSettings = S['JobSettings'];
export type JobCreate = S['JobCreate'];
export type JobOut = S['JobOut'];
export type OutputFile = S['OutputFile'];
export type DocumentOut = S['DocumentOut'];
export type Page = S['Page'];
export type PhysicalSizeBody = S['PhysicalSizeBody'];
export type Preset = S['Preset'];
export type PrinterProfile = S['PrinterProfile'];
export type PurgeOut = S['PurgeOut'];
export type Margins = S['Margins'];
export type PaperId = S['PaperId'];
export type Orientation = S['Orientation'];
export type SourceBox = S['SourceBox'];
export type JoinMethod = S['JoinMethod'];
export type Align = S['Align'];
export type SheetOrder = S['SheetOrder'];
export type NamingScheme = S['NamingScheme'];
export type BlankSensitivity = S['BlankSensitivity'];
export type TestSquare = S['TestSquare'];
export type AssemblyPage = S['AssemblyPage'];
export type Edge = S['Edge'];
export type Alternative = S['Alternative'];
export type Saving = S['Saving'];
export type RegionMm = S['RegionMm'];
export type Severity = S['Severity'];
export type Rotation = 0 | 90 | 180 | 270;
export type RollPaperMm = S['RollPaperMm'];

/** DXF / DWG options (`POST /documents/{id}/cad-options`, WP-17). */
export type CadColor = 'color' | 'black' | 'gray';
export interface CadOptions {
  layout?: string | null;
  /** Model-space units override (`mm`, `cm`, `m`, `in`, `ft`); null = the file's `$INSUNITS`. */
  units?: string | null;
  /** N of 1:N for model space; null = automatic. */
  scale?: number | null;
  color?: CadColor;
}
export interface CadLayout {
  name: string;
  kind: 'model' | 'paper';
  width_mm: number | null;
  height_mm: number | null;
}
/** `GET /documents/{id}/cad`: what the converter found and chose. */
export interface CadInfo {
  layouts: CadLayout[];
  layout: string;
  units: string;
  units_source: 'insunits' | 'assumed' | 'user';
  scale: number | null;
  scale_auto: boolean;
  color: CadColor;
}

/** COL-07 estimate in `JobOut.ink`. */
export interface InkEstimate {
  sheets: { id: string; coverage: number; equiv_cm2: number }[];
  total_coverage: number;
  total_equiv_cm2: number;
}

/** `POST /jobs/{id}/print` body (direct IPP printing, IMP-03). */
export interface PrintRequest {
  printer_id: string;
  copies: number;
  /** Sheet IDs ("A1"); omitted = every page of the PDF. */
  sheets?: string[];
}

/** `POST /printers/ipp-capabilities` answer (PAP-07), used to prefill a printer profile. */
export interface IppCapabilities {
  uri: string;
  name: string | null;
  papers: {
    paper_id: string;
    width_mm: number;
    height_mm: number;
    media: string | null;
    margins_mm: Margins | null;
  }[];
  default_margins_mm: Margins | null;
  color: boolean;
  document_formats: string[];
  accepts_pdf: boolean | null;
  accepts_pwg_raster: boolean | null;
  page_ranges: boolean;
}

/** What a finished `print` job reports in `JobOut.printing`. */
export interface PrintingInfo {
  render_job_id?: string;
  printer_id?: string;
  copies?: number;
  pages?: number[] | null;
  printer_job_id?: number | null;
  printer_job_state?: string | null;
}

/** `GET /papers` is not typed in the OpenAPI document (plain dicts). */
export interface PaperInfo {
  id: string;
  name: string;
  width_mm: number;
  height_mm: number;
  usage: string;
  width_in: number | null;
  height_in: number | null;
  pwg_name: string | null;
}

/** `GET /info` is not typed in the OpenAPI document either. */
export interface AppInfo {
  version: string;
  limits: {
    max_upload_mb: number;
    max_pages: number;
    max_megapixels: number;
    max_page_mm: number;
    max_sheets: number;
    preview_max_px: number;
    workers: number;
    retention_hours: number;
  };
  converters: { id: string; available: boolean; formats: string[] }[];
  auth_mode: string;
  default_locale: string;
  ipp_enabled: boolean;
}

export type DocumentStatus = 'processing' | 'password_required' | 'ready' | 'failed';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface ProgressEvent {
  stage: string;
  pct: number;
  message_key: string | null;
  params: Record<string, unknown>;
}
