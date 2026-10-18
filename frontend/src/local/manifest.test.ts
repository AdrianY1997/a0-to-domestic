import { describe, expect, it } from 'vitest';
import { DEFAULT_PACKAGES, resolveManifest, stagePct } from './manifest';

const PY = 'https://example.org/a0-to-domestic/py/';

// What tools/build_web_bundle.py writes today.
const real = {
  schema: 1,
  app_version: '0.1.0',
  pyodide: { version: '0.29.5', path: 'pyodide/', index: 'pyodide/pyodide.js' },
  packages: ['micropip', 'pymupdf', 'numpy'],
  extras: [
    { name: 'defusedxml', version: '0.7.1', wheel: 'defusedxml-0.7.1-py2.py3-none-any.whl' },
    { name: 'ezdxf', version: '1.4.5', wheel: 'ezdxf-1.4.5-py3-none-any.whl', optional: true },
  ],
  bundle: {
    file: 'a0td.zip',
    sha256: 'abc123',
    size: 1,
    modules: 2,
    entry: 'a0td.web.bridge',
    root: '/a0td',
  },
};

describe('resolveManifest', () => {
  it('reads the manifest written by the bundle builder', () => {
    const m = resolveManifest(real, PY);
    expect(m.bundleUrl).toBe(`${PY}a0td.zip?v=abc123`);
    expect(m.pyodideUrl).toBe(`${PY}pyodide/`);
    expect(m.packages).toEqual(['micropip', 'pymupdf', 'numpy']);
    expect(m.entry).toBe('a0td.web.bridge');
    expect(m.root).toBe('/a0td');
    expect(m.extras).toEqual([
      { url: `${PY}pyodide/defusedxml-0.7.1-py2.py3-none-any.whl`, optional: false },
      { url: `${PY}pyodide/ezdxf-1.4.5-py3-none-any.whl`, optional: true },
    ]);
  });

  it('has working defaults for an empty manifest', () => {
    const m = resolveManifest({}, PY);
    expect(m.bundleUrl).toBe(`${PY}a0td.zip`);
    expect(m.packages).toEqual(DEFAULT_PACKAGES);
    expect(m.extras).toEqual([]);
    expect(m.extractDir).toBe('/home/pyodide/src');
    expect(m.entry).toBe('a0td.web.bridge');
  });

  it('versions the bundle URL so a new build bypasses the cache', () => {
    expect(resolveManifest({ bundle: { sha256: 'a b' } }, PY).bundleUrl).toBe(
      `${PY}a0td.zip?v=a%20b`,
    );
  });

  it('resolves wheels given as paths, URLs or plain names', () => {
    const m = resolveManifest(
      { extras: ['x.whl', 'wheels/y.whl', 'https://cdn.example/z.whl'] },
      PY,
    );
    expect(m.extras.map((e) => e.url)).toEqual([
      `${PY}pyodide/x.whl`,
      `${PY}wheels/y.whl`,
      'https://cdn.example/z.whl',
    ]);
  });

  it('maps stage fractions onto a monotonic overall percentage', () => {
    expect(stagePct('runtime', 0)).toBe(0);
    expect(stagePct('packages', 0)).toBe(30);
    expect(stagePct('bundle', 1)).toBe(92);
    expect(stagePct('ready', 1)).toBe(100);
    expect(stagePct('packages', 5)).toBe(60);
  });
});
