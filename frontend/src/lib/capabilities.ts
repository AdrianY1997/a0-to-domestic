import { currentMode, type TransportMode } from '../api/transport';

/** What the running build can do. The Docker version has it all; the web version is client-only. */
export interface Capabilities {
  mode: TransportMode;
  /** Direct printing over IPP (SPEC 6.8). */
  ipp: boolean;
  /** Watched folder (inbox / outbox). */
  hotFolder: boolean;
  /** Password / token authentication and the login page. */
  auth: boolean;
  /** Prometheus metrics. */
  metrics: boolean;
  /** Everything is processed and stored in the browser. */
  clientOnly: boolean;
}

export function capabilitiesFor(mode: TransportMode): Capabilities {
  const local = mode === 'local';
  return {
    mode,
    ipp: !local,
    hotFolder: !local,
    auth: !local,
    metrics: !local,
    clientOnly: local,
  };
}

export const getCapabilities = (): Capabilities => capabilitiesFor(currentMode());
export const isLocalMode = (): boolean => currentMode() === 'local';

export const README_URL = 'https://github.com/AdrianY1997/a0-to-domestic#readme';

/**
 * Formats the in-browser engine does not read (SPEC 17bis): Office and ODF, legacy vector
 * formats, PostScript, CAD DWG, CorelDRAW, HPGL and DjVu. They need the Docker version.
 */
export const DOCKER_ONLY_EXTENSIONS = [
  'doc',
  'docx',
  'docm',
  'xls',
  'xlsx',
  'ppt',
  'pptx',
  'odt',
  'ods',
  'odp',
  'odg',
  'rtf',
  'vsd',
  'vsdx',
  'pub',
  'emf',
  'wmf',
  'ps',
  'eps',
  'ai',
  'dwg',
  'cdr',
  'hpgl',
  'plt',
  'djvu',
  'djv',
] as const;

const DOCKER_ONLY = new Set<string>(DOCKER_ONLY_EXTENSIONS);

export function extensionOf(filename: string): string {
  const i = filename.lastIndexOf('.');
  return i < 0 ? '' : filename.slice(i + 1).toLowerCase();
}

/** True when the web version cannot open the file (judged by extension only). */
export function isDockerOnlyFile(filename: string): boolean {
  return DOCKER_ONLY.has(extensionOf(filename));
}

/** Error codes that, in the web version, mean "use the Docker version". */
export const DOCKER_ONLY_CODES = new Set([
  'FORMAT_DOCKER_ONLY',
  'UNSUPPORTED_FORMAT',
  'FORMAT_NOT_AVAILABLE_IN_IMAGE',
]);
