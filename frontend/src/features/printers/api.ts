import { API_BASE, api } from '../../api/client';
import type { Margins, PrinterProfile } from '../../api/types';

/** Editable fields of a user profile (`POST`/`PUT /printers`). */
export interface PrinterInput {
  name: string;
  kind: PrinterProfile['kind'];
  supported_papers?: string[];
  default_margins: Margins;
  paper_margins: Record<string, Margins>;
  borderless: boolean;
  color: boolean;
  scale_kx: number;
  scale_ky: number;
  ipp_uri?: string | null;
  defaults?: Record<string, unknown>;
}

export interface CalibrationInput {
  paper: string;
  measured_x_mm: number;
  measured_y_mm: number;
  nominal_x_mm: number;
  nominal_y_mm: number;
  margins_mm: Margins;
}

export const createPrinter = (body: PrinterInput) => api.post<PrinterProfile>('/printers', body);
export const updatePrinter = (id: string, body: PrinterInput) =>
  api.put<PrinterProfile>(`/printers/${id}`, body);
export const deletePrinter = (id: string) => api.delete(`/printers/${id}`);
export const calibratePrinter = (id: string, body: CalibrationInput) =>
  api.post<PrinterProfile>(`/printers/${id}/calibration`, body);
export const calibrationPdfUrl = (id: string, paper: string, locale: string) =>
  `${API_BASE}/printers/${id}/calibration.pdf?paper=${encodeURIComponent(paper)}&locale=${encodeURIComponent(locale)}`;
