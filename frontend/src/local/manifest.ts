/**
 * `py/manifest.json`, written by `tools/build_web_bundle.py`:
 *
 *     { schema, app_version, pyodide: { version, path, index },
 *       packages: [names for loadPackage],
 *       extras: [{ name, version, wheel, optional? }],       // micropip wheels in <pyodide.path>
 *       bundle: { file, sha256, size, entry: "a0td.web.bridge", root: "/a0td" } }
 *
 * Every field is optional here so a leaner bundle still boots with the defaults below.
 */
export const DEFAULT_PACKAGES = ['micropip', 'pydantic', 'numpy', 'pillow', 'pymupdf'];
export const DEFAULT_ENTRY = 'a0td.web.bridge';

export interface ExtraWheel {
  url: string;
  /** A failure only removes a format (DXF, …) instead of stopping the boot. */
  optional: boolean;
}

export interface ResolvedManifest {
  /** Cache-busting token of the bundle (its sha256). */
  version: string;
  bundleUrl: string;
  /** URL of the vendored Pyodide folder, with a trailing slash. */
  pyodideUrl: string;
  packages: string[];
  extras: ExtraWheel[];
  /** Where the zip is unpacked and put on `sys.path`. */
  extractDir: string;
  /** Python module that exposes the bridge. */
  entry: string;
  /** The engine's data root in the worker FS. */
  root: string;
}

const isUrl = (s: string) => /^https?:\/\//i.test(s);

function strings(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === 'string') : undefined;
}

function extraList(value: unknown, pyodideUrl: string, pyBase: string): ExtraWheel[] {
  if (!Array.isArray(value)) return [];
  const out: ExtraWheel[] = [];
  for (const item of value) {
    const rec = (typeof item === 'string' ? { wheel: item } : item) as Record<string, unknown>;
    const file = rec?.wheel ?? rec?.url ?? rec?.file;
    if (typeof file !== 'string') continue;
    // Bare names live next to the Pyodide files; paths are relative to py/.
    const url = isUrl(file)
      ? file
      : file.includes('/')
        ? `${pyBase}${file}`
        : `${pyodideUrl}${file}`;
    out.push({ url, optional: rec?.optional === true });
  }
  return out;
}

/** `pyBase` is the absolute URL of the `py/` folder, with a trailing slash. */
export function resolveManifest(raw: unknown, pyBase: string): ResolvedManifest {
  const m = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const pyodide = (m.pyodide && typeof m.pyodide === 'object' ? m.pyodide : {}) as Record<
    string,
    unknown
  >;
  const bundle = (m.bundle && typeof m.bundle === 'object' ? m.bundle : {}) as Record<
    string,
    unknown
  >;
  const pyPath = typeof pyodide.path === 'string' ? pyodide.path : 'pyodide/';
  const pyodideUrl = isUrl(pyPath) ? pyPath : `${pyBase}${pyPath.replace(/^\/+/, '')}`;
  const file =
    typeof bundle.file === 'string'
      ? bundle.file
      : typeof m.bundle === 'string'
        ? m.bundle
        : 'a0td.zip';
  const version = String(bundle.sha256 ?? m.version ?? m.app_version ?? '');
  const base = isUrl(file) ? file : `${pyBase}${file}`;
  return {
    version,
    bundleUrl: version ? `${base}?v=${encodeURIComponent(version)}` : base,
    pyodideUrl: pyodideUrl.endsWith('/') ? pyodideUrl : `${pyodideUrl}/`,
    packages: strings(m.packages) ?? DEFAULT_PACKAGES,
    extras: extraList(m.extras, pyodideUrl.endsWith('/') ? pyodideUrl : `${pyodideUrl}/`, pyBase),
    extractDir: typeof m.extract_dir === 'string' ? m.extract_dir : '/home/pyodide/src',
    entry: typeof bundle.entry === 'string' ? bundle.entry : DEFAULT_ENTRY,
    root: typeof bundle.root === 'string' ? bundle.root : '/a0td',
  };
}

/** Overall boot percentage from the weight of each stage. */
export const STAGE_RANGES = {
  runtime: [0, 30],
  packages: [30, 60],
  extras: [60, 75],
  bundle: [75, 92],
  engine: [92, 100],
  ready: [100, 100],
} as const;

export function stagePct(stage: keyof typeof STAGE_RANGES, fraction: number): number {
  const [from, to] = STAGE_RANGES[stage];
  return Math.round(from + (to - from) * Math.min(1, Math.max(0, fraction)));
}
