import type { TFunction } from 'i18next';
import { ApiError } from '../api/client';
import { DOCKER_ONLY_CODES, isLocalMode } from './capabilities';

const KNOWN_CODES = new Set([
  'UNSUPPORTED_FORMAT',
  'FORMAT_NOT_AVAILABLE_IN_IMAGE',
  'FILE_TOO_LARGE',
  'TOO_MANY_PAGES',
  'PIXEL_LIMIT_EXCEEDED',
  'PAGE_TOO_LARGE',
  'PASSWORD_REQUIRED',
  'PASSWORD_INVALID',
  'PRINT_NOT_ALLOWED',
  'CORRUPT_FILE',
  'CONVERSION_FAILED',
  'CONVERSION_TIMEOUT',
  'INVALID_SETTINGS',
  'OVERLAP_TOO_LARGE',
  'CONTENT_AREA_TOO_SMALL',
  'SCALE_OUT_OF_RANGE',
  'TOO_MANY_SHEETS',
  'DOCUMENT_NOT_READY',
  'NOT_FOUND',
  'JOB_CANCELLED',
  'INTERRUPTED',
  'PRINTER_UNREACHABLE',
  'PRINT_REJECTED',
  'IPP_DISABLED',
  'DISCOVERY_UNAVAILABLE',
  'AUTH_REQUIRED',
  'CALIBRATION_OUT_OF_RANGE',
  'PRINTER_READONLY',
  'PRESET_READONLY',
  'INVALID_CREDENTIALS',
  'RATE_LIMITED',
  'CSRF_INVALID',
  'RENDER_UNAVAILABLE',
  'NETWORK_ERROR',
  'FORMAT_DOCKER_ONLY',
  'STORAGE_QUOTA_EXCEEDED',
  'ENGINE_UNAVAILABLE',
]);

/** Translated message for a server error code (params are interpolated), or a generic one. */
export function errorMessage(
  t: TFunction,
  code: string | null | undefined,
  params: Record<string, unknown> = {},
): string {
  // The web version cannot read these formats: say where they are available.
  if (code && isLocalMode() && DOCKER_ONLY_CODES.has(code))
    return t('errors.FORMAT_DOCKER_ONLY', params);
  if (code && KNOWN_CODES.has(code)) return t(`errors.${code}`, params);
  return t('errors.UNKNOWN');
}

export function apiErrorMessage(t: TFunction, err: unknown): string {
  if (err instanceof ApiError) return errorMessage(t, err.code, err.params);
  return t('errors.UNKNOWN');
}

/** Per-field messages of an `INVALID_SETTINGS` problem, e.g. `settings.tiling.overlap_mm: ...`. */
export function fieldErrors(err: unknown): string[] {
  if (!(err instanceof ApiError) || !Array.isArray(err.errors)) return [];
  return err.errors.slice(0, 3).map((e) => {
    const item = e as { field?: string; loc?: unknown[]; message?: string; msg?: string };
    const field = item.field ?? (item.loc ? item.loc.filter((p) => p !== 'body').join('.') : '');
    const message = item.message ?? item.msg ?? '';
    return field ? `${field}: ${message}` : message;
  });
}
